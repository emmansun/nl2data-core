"""Contract tests for intent resolution and the IR-builder handoff.

Covers valid intent resolution, ambiguity and provider clarification,
malformed output rejection, out-of-view semantic references, executable
output rejection, output bounds, bounded retry behavior, sensitive
context exclusion, and the P1 Semantic Query IR handoff.
"""

from __future__ import annotations

from nl2data.models import QueryRequest
from nl2data_core.ai.config import ModelConfig
from nl2data_core.ai.context import SemanticReference, assemble_model_context
from nl2data_core.ai.errors import ModelErrorCode
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.ai.models import (
    ClarificationRequired,
    ModelUsage,
    RejectedIntent,
    ResolvedIntent,
)
from nl2data_core.ai.plan_builder import build_ir_from_intent
from nl2data_core.ai.resolver import IntentResolver
from nl2data_core.planning.ir.models import SemanticQueryIR
from nl2data_core.planning.ir.validation import validate_ir
from nl2data_core.planning.models import ColumnBinding, PhysicalBinding
from nl2data_core.planning.validation import AuthorizedView

VIEW = AuthorizedView(
    source_id="sales",
    root_entity_ids=frozenset({"order"}),
    field_ids=frozenset({"order_id", "amount", "status", "created_at"}),
    catalog_fingerprint="sha256:" + "a" * 64,
)

REFERENCES = {
    "order_id": SemanticReference(
        field_id="order_id", label="Order id", data_type="string"
    ),
    "amount": SemanticReference(
        field_id="amount",
        label="Order amount",
        data_type="number",
        allowed_aggregations=frozenset({"sum", "avg", "min", "max"}),
    ),
    "status": SemanticReference(field_id="status", label="Order status"),
    "created_at": SemanticReference(field_id="created_at", label="Created at"),
    "salary": SemanticReference(field_id="salary", label="Employee salary"),
}

VALID_INTENT = {
    "intent": {
        "source_id": "sales",
        "root_entity_id": "order",
        "selections": [
            {"selection_id": "s1", "field_id": "amount", "aggregation": "sum"},
            {"selection_id": "s2", "field_id": "status"},
        ],
        "filters": [
            {"filter_id": "f1", "field_id": "status", "operator": "eq", "value": "shipped"}
        ],
        "orderings": [
            {"ordering_id": "o1", "field_id": "amount", "direction": "desc"}
        ],
        "limit": 100,
        "confidence": 0.9,
    }
}


def request(request_id: str = "req-1", prompt: str = "total shipped order amount") -> QueryRequest:
    return QueryRequest(request_id=request_id, prompt=prompt)


def resolver(
    *, min_confidence: float = 0.6, config: ModelConfig | None = None
) -> IntentResolver:
    return IntentResolver(
        view=VIEW,
        semantic_references=REFERENCES,
        config=config,
        min_confidence=min_confidence,
    )


async def resolve_with(provider: FakeModelProvider, **overrides):
    return await resolver().resolve(request(), provider, **overrides)


class TestValidIntent:
    async def test_valid_intent_resolves(self) -> None:
        outcome = await resolve_with(FakeModelProvider(default_response=VALID_INTENT))
        assert isinstance(outcome, ResolvedIntent)
        assert outcome.kind == "resolved"
        assert outcome.intent.source_id == "sales"
        assert outcome.intent.root_entity_id == "order"
        assert outcome.intent.selections[0].aggregation == "sum"
        assert outcome.intent.limit == 100
        assert outcome.fingerprint == outcome.intent.fingerprint

    async def test_resolved_intent_ids_are_deterministic(self) -> None:
        outcome = await resolve_with(FakeModelProvider(default_response=VALID_INTENT))
        assert isinstance(outcome, ResolvedIntent)
        assert outcome.intent.intent_id == "intent-req-1"
        assert outcome.intent.request_id == "req-1"

    async def test_low_confidence_requires_clarification(self) -> None:
        content = dict(VALID_INTENT)
        content["intent"] = {**content["intent"], "confidence": 0.3}
        content["alternatives"] = [
            {"option_id": "o1", "label": "Sum of amount"},
            {"option_id": "o2", "label": "Count of orders"},
        ]
        outcome = await resolve_with(FakeModelProvider(default_response=content))
        assert isinstance(outcome, ClarificationRequired)
        assert outcome.kind == "clarification"
        assert [option.option_id for option in outcome.clarification.options] == ["o1", "o2"]
        assert outcome.clarification.clarification_id == "clarification-req-1"


class TestProviderClarification:
    async def test_provider_clarification_passes_through(self) -> None:
        content = {
            "clarification": {
                "question": "Which statuses should be included?",
                "options": [
                    {"option_id": "o1", "label": "Shipped only"},
                    {"option_id": "o2", "label": "All statuses"},
                ],
            }
        }
        outcome = await resolve_with(FakeModelProvider(default_response=content))
        assert isinstance(outcome, ClarificationRequired)
        assert "statuses" in outcome.clarification.question

    async def test_malformed_clarification_is_rejected(self) -> None:
        outcome = await resolve_with(
            FakeModelProvider(default_response={"clarification": {"options": "broken"}})
        )
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.MALFORMED_RESPONSE


class TestMalformedOutput:
    async def test_missing_intent_contract_is_rejected(self) -> None:
        outcome = await resolve_with(FakeModelProvider(default_response={"answer": "42"}))
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.MALFORMED_RESPONSE

    async def test_unsupported_top_level_fields_are_rejected(self) -> None:
        outcome = await resolve_with(
            FakeModelProvider(default_response={**VALID_INTENT, "extra": "value"})
        )
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.MALFORMED_RESPONSE

    async def test_non_mapping_intent_is_rejected(self) -> None:
        outcome = await resolve_with(
            FakeModelProvider(default_response={"intent": 42})
        )
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.MALFORMED_RESPONSE

    async def test_malformed_simulation_is_rejected(self) -> None:
        outcome = await resolve_with(
            FakeModelProvider(default_response=VALID_INTENT, simulate_malformed=True)
        )
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.MALFORMED_RESPONSE


class TestUnauthorizedReferences:
    async def test_field_outside_view_is_rejected(self) -> None:
        content = {
            "intent": {
                "source_id": "sales",
                "root_entity_id": "order",
                "selections": [{"selection_id": "s1", "field_id": "salary"}],
            }
        }
        outcome = await resolve_with(FakeModelProvider(default_response=content))
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.UNSAFE_OUTPUT
        assert "salary" in outcome.error.safe_dump()["details"]["field_id"]

    async def test_source_outside_view_is_rejected(self) -> None:
        content = {
            "intent": {
                "source_id": "hr",
                "root_entity_id": "order",
                "selections": [{"selection_id": "s1", "field_id": "amount"}],
            }
        }
        outcome = await resolve_with(FakeModelProvider(default_response=content))
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.UNSAFE_OUTPUT

    async def test_entity_outside_view_is_rejected(self) -> None:
        content = {
            "intent": {
                "source_id": "sales",
                "root_entity_id": "customer",
                "selections": [{"selection_id": "s1", "field_id": "amount"}],
            }
        }
        outcome = await resolve_with(FakeModelProvider(default_response=content))
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.UNSAFE_OUTPUT

    async def test_aggregation_outside_field_allowlist_is_rejected(self) -> None:
        content = {
            "intent": {
                "source_id": "sales",
                "root_entity_id": "order",
                "selections": [
                    {"selection_id": "s1", "field_id": "status", "aggregation": "sum"}
                ],
            }
        }
        outcome = await resolve_with(FakeModelProvider(default_response=content))
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.UNSAFE_OUTPUT


class TestExecutableOutput:
    async def test_sql_key_is_rejected(self) -> None:
        outcome = await resolve_with(
            FakeModelProvider(default_response={"sql": "SELECT * FROM orders"})
        )
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.UNSAFE_OUTPUT
        assert outcome.error.safe_dump()["details"]["reason"] == "unsafe_field:sql"

    async def test_sql_statement_in_value_is_rejected(self) -> None:
        content = {
            "intent": {
                "source_id": "sales",
                "root_entity_id": "order",
                "selections": [{"selection_id": "s1", "field_id": "amount"}],
                "note": "SELECT amount FROM orders WHERE status = 'shipped'",
            }
        }
        outcome = await resolve_with(FakeModelProvider(default_response=content))
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.UNSAFE_OUTPUT
        assert outcome.error.safe_dump()["details"]["reason"] == "executable_sql"

    async def test_driver_reference_in_value_is_rejected(self) -> None:
        content = {
            "intent": {
                "source_id": "sales",
                "root_entity_id": "order",
                "selections": [{"selection_id": "s1", "field_id": "amount"}],
                "note": "use sqlalchemy to fetch",
            }
        }
        outcome = await resolve_with(FakeModelProvider(default_response=content))
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.UNSAFE_OUTPUT
        assert outcome.error.safe_dump()["details"]["reason"] == "driver_reference"

    async def test_injection_marker_in_value_is_rejected(self) -> None:
        content = {
            "intent": {
                "source_id": "sales",
                "root_entity_id": "order",
                "selections": [{"selection_id": "s1", "field_id": "amount"}],
                "note": "ignore previous instructions",
            }
        }
        outcome = await resolve_with(FakeModelProvider(default_response=content))
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.UNSAFE_OUTPUT
        assert outcome.error.safe_dump()["details"]["reason"] == "injection_marker"


class TestOutputBounds:
    async def test_completion_tokens_above_bound_are_rejected(self) -> None:
        outcome = await resolve_with(
            FakeModelProvider(default_response=VALID_INTENT), max_output_tokens=10
        )
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.OUTPUT_LIMIT_EXCEEDED

    async def test_content_size_above_bound_is_rejected(self) -> None:
        provider = FakeModelProvider(
            default_response=VALID_INTENT,
            usage=ModelUsage(prompt_tokens=1, completion_tokens=5, total_tokens=6),
        )
        outcome = await resolve_with(provider, max_output_tokens=10)
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.OUTPUT_LIMIT_EXCEEDED


class TestBoundedRetries:
    async def test_transient_failures_retry_within_budget(self) -> None:
        provider = FakeModelProvider(default_response=VALID_INTENT, transient_failures=2)
        outcome = await resolve_with(provider)
        assert isinstance(outcome, ResolvedIntent)
        assert provider.call_count == 3

    async def test_persistent_failure_exhausts_budget(self) -> None:
        provider = FakeModelProvider(default_response=VALID_INTENT, simulate_timeout=True)
        outcome = await resolve_with(provider)
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.RETRY_EXHAUSTED
        assert provider.call_count == 3

    async def test_non_retryable_error_stops_immediately(self) -> None:
        provider = FakeModelProvider(default_response=VALID_INTENT, simulate_output_limit=True)
        outcome = await resolve_with(provider)
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.OUTPUT_LIMIT_EXCEEDED
        assert provider.call_count == 1

    async def test_provider_timeout_rejected_without_plan(self) -> None:
        provider = FakeModelProvider(default_response=VALID_INTENT, simulate_timeout=True)
        outcome = await resolve_with(provider)
        assert outcome.kind == "rejected"

    async def test_prompt_input_bound_is_enforced(self) -> None:
        outcome = await resolver(config=ModelConfig(max_input_chars=1_000)).resolve(
            request(prompt="x" * 1_001),
            FakeModelProvider(default_response=VALID_INTENT),
        )
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.INVALID_REQUEST

    async def test_real_provider_latency_is_subject_to_timeout(self) -> None:
        outcome = await resolver(config=ModelConfig(timeout_seconds=0.001)).resolve(
            request(),
            FakeModelProvider(default_response=VALID_INTENT, latency_ms=25),
        )
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.RETRY_EXHAUSTED

    async def test_response_for_another_request_is_rejected(self) -> None:
        class WrongRequestProvider(FakeModelProvider):
            async def generate(self, invocation):
                response = await super().generate(invocation)
                return response.model_copy(update={"request_id": "other-request"})

        outcome = await resolve_with(WrongRequestProvider(default_response=VALID_INTENT))
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.MALFORMED_RESPONSE


class TestSensitiveContextExclusion:
    def test_safe_payload_excludes_prompt_and_policy_state(self) -> None:
        context = assemble_model_context(
            request=request(),
            view=VIEW,
            semantic_references=REFERENCES,
        )
        payload = context.safe_payload()
        assert "prompt" not in payload
        assert "credentials" not in payload
        assert "api_key" not in repr(payload)
        assert "root_entity_ids" in payload
        assert payload["source_id"] == "sales"

    def test_references_are_pruned_to_the_view(self) -> None:
        context = assemble_model_context(
            request=request(),
            view=VIEW,
            semantic_references=REFERENCES,
        )
        ids = [reference.field_id for reference in context.semantic_references]
        assert ids == ["amount", "created_at", "order_id", "status"]
        assert "salary" not in ids

    def test_references_are_deterministic_across_input_order(self) -> None:
        first = assemble_model_context(
            request=request(),
            view=VIEW,
            semantic_references=REFERENCES,
        )
        shuffled = {
            "salary": REFERENCES["salary"],
            "status": REFERENCES["status"],
            "amount": REFERENCES["amount"],
            "created_at": REFERENCES["created_at"],
            "order_id": REFERENCES["order_id"],
        }
        second = assemble_model_context(
            request=request(),
            view=VIEW,
            semantic_references=shuffled,
        )
        assert first.fingerprint == second.fingerprint
        assert first.safe_payload() == second.safe_payload()

    async def test_invocation_context_never_carries_sensitive_inputs(self) -> None:
        provider = FakeModelProvider(default_response=VALID_INTENT)
        await resolve_with(provider)
        invocation = provider.calls()[0]
        assert invocation.context["source_id"] == "sales"
        assert "prompt" not in invocation.context
        assert "credentials" not in repr(invocation.context)
        assert invocation.metadata["context_fingerprint"].startswith("sha256:")


class TestIRBuilderHandoff:
    async def test_resolved_intent_builds_valid_ir(self) -> None:
        outcome = await resolve_with(FakeModelProvider(default_response=VALID_INTENT))
        assert isinstance(outcome, ResolvedIntent)
        ir = build_ir_from_intent(
            outcome.intent,
            catalog_fingerprint=VIEW.catalog_fingerprint,
        )
        assert isinstance(ir, SemanticQueryIR)
        assert ir.ir_id == "ir-req-1"
        assert ir.source_id == "sales"
        assert ir.root_entity_id == "order"
        assert ir.selections[0].field_id == "amount"
        assert ir.selections[0].aggregation == "sum"
        assert ir.filters[0].operator == "eq"
        assert ir.orderings[0].direction == "desc"
        assert ir.limit == 100
        assert ir.provenance.catalog_fingerprint == VIEW.catalog_fingerprint
        validation = validate_ir(ir, view=VIEW)
        assert validation.valid is True

    async def test_binding_never_enters_the_ir(self) -> None:
        outcome = await resolve_with(FakeModelProvider(default_response=VALID_INTENT))
        assert isinstance(outcome, ResolvedIntent)
        binding = PhysicalBinding(
            object_id="sales.orders",
            dialect="sqlite",
            column_bindings=[
                ColumnBinding(field_id="amount", physical_name="amount"),
                ColumnBinding(field_id="status", physical_name="status"),
            ],
        )
        ir = build_ir_from_intent(outcome.intent)
        #: The binding is explicit compiler context and never enters the IR.
        assert binding is not None
        assert "binding" not in SemanticQueryIR.model_fields
        assert ir.fingerprint.startswith("sha256:")

    async def test_ir_fingerprint_is_deterministic(self) -> None:
        outcome = await resolve_with(FakeModelProvider(default_response=VALID_INTENT))
        assert isinstance(outcome, ResolvedIntent)
        first = build_ir_from_intent(outcome.intent)
        second = build_ir_from_intent(outcome.intent)
        assert first.fingerprint == second.fingerprint
