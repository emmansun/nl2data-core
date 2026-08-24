"""End-to-end integration tests for the opt-in AI workflow path.

Covers the full path from public QueryRequest through the fake model
provider, intent validation, Semantic Query IR handoff, and the
existing governed execution boundary - plus the preserved P1
structured-IR and not-configured fallbacks.
"""

from __future__ import annotations

from pathlib import Path

from nl2data import (
    ErrorCode,
    NL2DataEngine,
    OutcomeStatus,
    QueryRequest,
)
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.ai.context import SemanticReference
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.ai.workflow import AIWorkflowRunner
from nl2data_core.config.loader import load_config
from nl2data_core.fixtures import SQLiteFixtureProfile
from nl2data_core.governance.models import PolicyScope
from nl2data_core.planning.ir.models import (
    IRFilter,
    IROrdering,
    IRProvenance,
    IRSelection,
    SemanticQueryIR,
)
from nl2data_core.planning.models import ColumnBinding, PhysicalBinding
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.workflow.durable import IdempotencyStatus
from nl2data_core.workflow.runner import QueryExecutionRunner, StaticPlanResolver
from nl2data_core.workflow.runtime import DeterministicWorkflowRuntime
from nl2data_core.workflow.sqlite_store import SQLiteStateStore

FIELDS = frozenset({"order_id", "customer_id", "amount", "region", "status", "created_at"})

REFERENCES = {
    "order_id": SemanticReference(field_id="order_id", label="Order id"),
    "amount": SemanticReference(
        field_id="amount",
        label="Order amount",
        allowed_aggregations=frozenset({"sum", "avg", "min", "max"}),
    ),
    "region": SemanticReference(field_id="region", label="Region"),
    "status": SemanticReference(field_id="status", label="Status"),
    "created_at": SemanticReference(field_id="created_at", label="Created at"),
}

BINDING = PhysicalBinding(
    object_id="orders",
    dialect="sqlite",
    column_bindings=(
        ColumnBinding(field_id="order_id", physical_name="order_id"),
        ColumnBinding(field_id="amount", physical_name="amount"),
        ColumnBinding(field_id="region", physical_name="region"),
        ColumnBinding(field_id="status", physical_name="status"),
        ColumnBinding(field_id="created_at", physical_name="created_at"),
    ),
)

VALID_INTENT = {
    "intent": {
        "source_id": "sales",
        "root_entity_id": "order",
        "selections": [
            {"selection_id": "s1", "field_id": "order_id"},
            {"selection_id": "s2", "field_id": "amount"},
        ],
        "filters": [{"filter_id": "f1", "field_id": "region", "operator": "eq", "value": "emea"}],
        "orderings": [{"ordering_id": "o1", "field_id": "order_id", "direction": "desc"}],
        "limit": 10,
        "confidence": 0.95,
    }
}


def make_policy_scope(**overrides) -> PolicyScope:
    values = {
        "policy_id": "fixture-policy",
        "source_ids": frozenset({"sales"}),
        "resource_ids": frozenset({"orders"}),
        "operation_ids": frozenset({"select"}),
        "field_ids": FIELDS,
    }
    values.update(overrides)
    return PolicyScope(**values)


def make_view(**overrides) -> AuthorizedView:
    values = {
        "source_id": "sales",
        "root_entity_ids": frozenset({"order"}),
        "field_ids": FIELDS,
    }
    values.update(overrides)
    return AuthorizedView(**values)


def make_adapter(tmp_path: Path, **overrides) -> SqlQueryAdapter:
    values = {
        "dialect": "sqlite",
        "db_path": tmp_path / "fixture.db",
        "allowed_objects": frozenset({"orders"}),
        "allowed_columns": FIELDS,
        "max_rows": 100,
    }
    values.update(overrides)
    return SqlQueryAdapter(**values)


def make_execution(tmp_path: Path, **overrides) -> QueryExecutionRunner:
    fixture = SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")
    fixture.provision()
    values = {
        "adapter": make_adapter(tmp_path),
        "policy_scope": make_policy_scope(),
        "view": make_view(),
        "plan_resolver": StaticPlanResolver(None),
        "binding": BINDING,
    }
    values.update(overrides)
    return QueryExecutionRunner(**values)


def make_fixed_ir() -> SemanticQueryIR:
    """A P1 structured IR equivalent to the AI intent, with aliases."""
    return SemanticQueryIR(
        ir_id="ir-fallback",
        source_id="sales",
        root_entity_id="order",
        selections=(
            IRSelection(selection_id="s1", field_id="order_id", alias="oid"),
            IRSelection(selection_id="s2", field_id="amount", alias="amt"),
        ),
        filters=(
            IRFilter(filter_id="f1", field_id="region", operator="eq", value="emea"),
        ),
        orderings=(
            IROrdering(ordering_id="o1", field_id="order_id", direction="desc"),
        ),
        limit=10,
        provenance=IRProvenance(source_id="sales", root_entity_id="order"),
    )


def make_ai_runner(
    tmp_path: Path,
    *,
    provider=None,
    execution: QueryExecutionRunner | None = None,
    **overrides,
) -> AIWorkflowRunner:
    values = {
        "provider": provider or FakeModelProvider(default_response=VALID_INTENT),
        "execution": execution or make_execution(tmp_path),
        "semantic_references": REFERENCES,
        "binding": BINDING,
    }
    values.update(overrides)
    return AIWorkflowRunner(**values)


def request(request_id: str = "ai-1") -> QueryRequest:
    return QueryRequest(request_id=request_id, prompt="top 10 order amounts in emea")


class TestAiSuccessPath:
    async def test_ai_query_succeeds_through_governed_boundary(self, tmp_path: Path) -> None:
        outcome = await make_ai_runner(tmp_path).execute(request())
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert outcome.error is None
        assert outcome.result is not None
        assert outcome.result.column_names == ("order_id", "amount")
        assert outcome.result.rows == (
            (18, 180.0),
            (17, 170.0),
            (16, 160.0),
            (15, 150.0),
            (14, 140.0),
            (13, 130.0),
            (6, 60.0),
            (5, 50.0),
            (4, 40.0),
            (3, 30.0),
        )
        assert outcome.result.fingerprint is not None

    async def test_ai_results_are_repeatable(self, tmp_path: Path) -> None:
        runner = make_ai_runner(tmp_path)
        first = await runner.execute(request())
        second = await runner.execute(request())
        assert first.status == OutcomeStatus.SUCCEEDED
        assert first.result is not None and second.result is not None
        assert first.result.fingerprint == second.result.fingerprint
        assert first.result.rows == second.result.rows

    async def test_ai_path_records_one_attempt(self, tmp_path: Path) -> None:
        provider = FakeModelProvider(default_response=VALID_INTENT)
        outcome = await make_ai_runner(tmp_path, provider=provider).execute(request())
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert provider.call_count == 1


class TestAiRejectedPaths:
    async def test_unsafe_output_is_rejected_before_adapter(self, tmp_path: Path) -> None:
        provider = FakeModelProvider(default_response={"sql": "SELECT * FROM orders"})
        outcome = await make_ai_runner(tmp_path, provider=provider).execute(request())
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.result is None
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.MODEL_INVOCATION_FAILED
        assert outcome.error.details["model_code"] == "UNSAFE_OUTPUT"

    async def test_malformed_output_is_rejected(self, tmp_path: Path) -> None:
        provider = FakeModelProvider(default_response={"intent": {"selections": "broken"}})
        outcome = await make_ai_runner(tmp_path, provider=provider).execute(request())
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.MODEL_INVOCATION_FAILED
        assert outcome.error.details["model_code"] == "MALFORMED_RESPONSE"

    async def test_clarification_is_a_structured_public_outcome(
        self, tmp_path: Path
    ) -> None:
        provider = FakeModelProvider(
            default_response={
                "clarification": {
                    "question": "Which region should be included?",
                    "options": [
                        {"option_id": "o1", "label": "EMEA"},
                        {"option_id": "o2", "label": "APAC"},
                    ],
                }
            }
        )
        outcome = await make_ai_runner(tmp_path, provider=provider).execute(request())
        assert outcome.status == OutcomeStatus.CLARIFICATION
        assert outcome.result is None
        assert outcome.error is None
        assert outcome.clarification is not None
        assert outcome.clarification.question == "Which region should be included?"
        assert [option.label for option in outcome.clarification.options] == ["EMEA", "APAC"]

    async def test_timeout_is_rejected_after_bounded_retries(self, tmp_path: Path) -> None:
        provider = FakeModelProvider(default_response=VALID_INTENT, simulate_timeout=True)
        outcome = await make_ai_runner(tmp_path, provider=provider).execute(request())
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.MODEL_INVOCATION_FAILED
        assert outcome.error.details["model_code"] == "RETRY_EXHAUSTED"
        assert provider.call_count == 3

    async def test_governance_denial_still_applies_to_ai_plans(self, tmp_path: Path) -> None:
        scope = make_policy_scope(field_ids=FIELDS - {"amount"})
        execution = make_execution(tmp_path, policy_scope=scope)
        outcome = await make_ai_runner(tmp_path, execution=execution).execute(request())
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.GOVERNANCE_DENIED


class TestFallbacks:
    async def test_without_provider_p1_structured_ir_path_is_preserved(
        self, tmp_path: Path
    ) -> None:
        execution = make_execution(
            tmp_path,
            plan_resolver=StaticPlanResolver(make_fixed_ir()),
        )
        runner = AIWorkflowRunner(provider=None, execution=execution)
        assert runner.is_configured() is False
        outcome = await runner.execute(request())
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert outcome.result is not None
        assert outcome.result.column_names == ("oid", "amt")

    async def test_without_governed_execution_not_configured_is_preserved(
        self, tmp_path: Path
    ) -> None:
        runner = make_ai_runner(tmp_path, execution=QueryExecutionRunner())
        assert runner.is_configured() is False
        outcome = await runner.execute(request())
        assert outcome.status == OutcomeStatus.NOT_CONFIGURED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.NOT_CONFIGURED

    async def test_close_is_idempotent_and_releases_provider(self, tmp_path: Path) -> None:
        provider = FakeModelProvider(default_response=VALID_INTENT)
        runner = make_ai_runner(tmp_path, provider=provider)
        await runner.close()
        await runner.close()
        assert provider.closed is True


class TestFacadeDelegation:
    async def test_facade_delegates_to_the_deterministic_runtime(
        self, tmp_path: Path
    ) -> None:
        provider = FakeModelProvider(default_response=VALID_INTENT)
        runner = make_ai_runner(tmp_path, provider=provider)
        assert isinstance(runner.runtime, DeterministicWorkflowRuntime)
        outcome = await runner.execute(request())
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert provider.call_count == 1

    async def test_facade_forwards_durable_state_store(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "facade-durable.db")
        try:
            provider = FakeModelProvider(default_response=VALID_INTENT)
            runner = make_ai_runner(
                tmp_path,
                provider=provider,
                state_store=store,
            )
            first = await runner.execute(request("r-dup"))
            assert first.status == OutcomeStatus.SUCCEEDED
            assert provider.call_count == 1

            idem = store.get_idempotency("r-dup")
            assert idem is not None
            assert idem.status == IdempotencyStatus.COMPLETED

            second = await runner.execute(request("r-dup"))
            assert second.status == OutcomeStatus.REJECTED
            assert second.error is not None
            assert second.error.code == ErrorCode.DUPLICATE_REQUEST
            assert provider.call_count == 1  # no re-execution
        finally:
            store.close()

    async def test_facade_forwards_approval_required_hook(self, tmp_path: Path) -> None:
        runner = make_ai_runner(
            tmp_path,
            approval_required=lambda ir: True,
        )
        outcome = await runner.execute(request())
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.APPROVAL_REQUIRED


class TestEngineIntegration:
    async def test_engine_routes_ai_workflow(self, tmp_path: Path) -> None:
        config = load_config({"schema_version": 1, "service": {"name": "ai-e2e"}})
        engine = NL2DataEngine(
            config=config,
            workflow_port=make_ai_runner(tmp_path),
        )
        await engine.initialize()
        outcome = await engine.query(request())
        assert outcome.status == OutcomeStatus.SUCCEEDED
        await engine.close()

    async def test_engine_ai_fallback_reports_not_configured(self) -> None:
        config = load_config({"schema_version": 1, "service": {"name": "ai-e2e"}})
        engine = NL2DataEngine(
            config=config,
            workflow_port=AIWorkflowRunner(
                provider=None,
                execution=QueryExecutionRunner(),
            ),
        )
        await engine.initialize()
        outcome = await engine.query(request())
        assert outcome.status == OutcomeStatus.NOT_CONFIGURED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.NOT_CONFIGURED
