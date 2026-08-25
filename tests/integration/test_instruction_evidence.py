"""Integration tests proving provider-neutral instruction identity.

The instruction bundle identity must survive every evidence surface -
model invocation metadata, durable workflow gate evidence, and AI
evaluation protected evidence - without raw prompt or instruction text
crossing any boundary.
"""

from __future__ import annotations

from pathlib import Path

from nl2data import OutcomeStatus, QueryContext, QueryRequest
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.ai.context import SemanticReference, assemble_model_context
from nl2data_core.ai.evaluation.cases import build_ai_cases, build_ai_dataset
from nl2data_core.ai.evaluation.models import AIEvaluationReport
from nl2data_core.ai.evaluation.runner import AIEvaluationRunner, evidence_is_redacted
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.ai.instructions import (
    DEFAULT_ROLE,
    OutputContract,
    assemble_instruction_bundle,
    instruction_evidence_fingerprint,
)
from nl2data_core.fixtures import SQLiteFixtureProfile
from nl2data_core.governance.models import PolicyScope
from nl2data_core.planning.models import ColumnBinding, PhysicalBinding
from nl2data_core.planning.validation import AuthorizedView
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

EVALUATION_VIEW = AuthorizedView(
    source_id="sales",
    root_entity_ids=frozenset({"order"}),
    field_ids=frozenset({"order_id", "amount", "status", "created_at"}),
    catalog_fingerprint="sha256:" + "a" * 64,
)

EVALUATION_REFERENCES = {
    "order_id": SemanticReference(field_id="order_id", label="Order id"),
    "amount": SemanticReference(
        field_id="amount",
        label="Order amount",
        allowed_aggregations=frozenset({"sum", "avg"}),
    ),
    "status": SemanticReference(field_id="status", label="Order status"),
    "created_at": SemanticReference(field_id="created_at", label="Created at"),
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


def make_execution(tmp_path: Path, **overrides) -> QueryExecutionRunner:
    fixture = SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")
    fixture.provision()
    values = {
        "adapter": SqlQueryAdapter(
            dialect="sqlite",
            db_path=tmp_path / "fixture.db",
            allowed_objects=frozenset({"orders"}),
            allowed_columns=FIELDS,
            max_rows=100,
        ),
        "policy_scope": make_policy_scope(),
        "view": make_view(),
        "plan_resolver": StaticPlanResolver(None),
        "binding": BINDING,
    }
    values.update(overrides)
    return QueryExecutionRunner(**values)


def make_runtime(
    tmp_path: Path,
    *,
    execution: QueryExecutionRunner,
    state_store: SQLiteStateStore,
    provider: FakeModelProvider | None = None,
    **overrides,
) -> DeterministicWorkflowRuntime:
    values = {
        "provider": provider or FakeModelProvider(default_response=VALID_INTENT),
        "execution": execution,
        "semantic_references": REFERENCES,
        "binding": BINDING,
        "state_store": state_store,
    }
    values.update(overrides)
    return DeterministicWorkflowRuntime(**values)


def request(request_id: str, workflow_id: str) -> QueryRequest:
    return QueryRequest(
        request_id=request_id,
        prompt="top 10 order amounts in emea",
        context=QueryContext(request_id=request_id, workflow_id=workflow_id),
    )


def expected_instruction_evidence(
    query: QueryRequest,
    policy_scope: PolicyScope | None = None,
) -> str:
    """The instruction evidence identity the runtime should record."""
    context = assemble_model_context(
        request=query,
        view=make_view(),
        semantic_references=REFERENCES,
    )
    bundle = assemble_instruction_bundle(
        request=query,
        context=context,
        view=make_view(),
        policy_fingerprint=(policy_scope or make_policy_scope()).policy_fingerprint,
    )
    return instruction_evidence_fingerprint(bundle)


async def run_evaluation(run_id: str = "run-ev-1") -> AIEvaluationReport:
    runner = AIEvaluationRunner(
        dataset=build_ai_dataset(),
        run_id=run_id,
        view=EVALUATION_VIEW,
        semantic_references=EVALUATION_REFERENCES,
    )
    return await runner.run()


class TestWorkflowEvidence:
    async def test_instruction_identity_survives_into_durable_evidence(
        self, tmp_path: Path
    ) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            runtime = make_runtime(
                tmp_path,
                execution=make_execution(tmp_path),
                state_store=store,
            )
            outcome = await runtime.execute(request("req-ev-1", "wf-ev-1"))
            assert outcome.status == OutcomeStatus.SUCCEEDED

            checkpoint = store.get_checkpoint("wf-ev-1", "req-ev-1")
            assert checkpoint is not None
            assert checkpoint.gate_evidence_fingerprints
            assert expected_instruction_evidence(request("req-ev-1", "wf-ev-1")) in (
                checkpoint.gate_evidence_fingerprints
            )
            for fingerprint in checkpoint.gate_evidence_fingerprints:
                assert fingerprint.startswith("sha256:")
        finally:
            store.close()

    async def test_instruction_identity_is_repeatable(self, tmp_path: Path) -> None:
        evidence: list[str] = []
        for index in range(2):
            store = SQLiteStateStore(tmp_path / f"durable-{index}.db")
            try:
                runtime = make_runtime(
                    tmp_path,
                    execution=make_execution(tmp_path),
                    state_store=store,
                )
                query = request(f"req-rep-{index}", f"wf-rep-{index}")
                outcome = await runtime.execute(query)
                assert outcome.status == OutcomeStatus.SUCCEEDED
                checkpoint = store.get_checkpoint(f"wf-rep-{index}", f"req-rep-{index}")
                assert checkpoint is not None
                evidence.append(expected_instruction_evidence(query))
                assert expected_instruction_evidence(query) in (
                    checkpoint.gate_evidence_fingerprints
                )
            finally:
                store.close()
        assert evidence[0] == evidence[1]

    async def test_instruction_identity_changes_with_policy_context(
        self, tmp_path: Path
    ) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            runtime = make_runtime(
                tmp_path,
                execution=make_execution(
                    tmp_path,
                    policy_scope=make_policy_scope(policy_id="stricter-policy"),
                ),
                state_store=store,
            )
            query = request("req-pol", "wf-pol")
            outcome = await runtime.execute(query)
            assert outcome.status == OutcomeStatus.SUCCEEDED
            checkpoint = store.get_checkpoint("wf-pol", "req-pol")
            assert checkpoint is not None
            stricter = expected_instruction_evidence(
                query, policy_scope=make_policy_scope(policy_id="stricter-policy")
            )
            assert stricter in checkpoint.gate_evidence_fingerprints
            assert stricter != expected_instruction_evidence(request("req-other", "wf-other"))
        finally:
            store.close()

    async def test_invocation_carries_identity_without_prompt_or_text(
        self, tmp_path: Path
    ) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            provider = FakeModelProvider(default_response=VALID_INTENT)
            runtime = make_runtime(
                tmp_path,
                execution=make_execution(tmp_path),
                state_store=store,
                provider=provider,
            )
            outcome = await runtime.execute(request("req-inv", "wf-inv"))
            assert outcome.status == OutcomeStatus.SUCCEEDED
            assert provider.call_count == 1

            invocation = provider.calls()[0]
            assert invocation.instruction is not None
            assert invocation.instruction.fingerprint.startswith("sha256:")
            assert (
                invocation.metadata["instruction_fingerprint"]
                == invocation.instruction.fingerprint
            )
            assert invocation.metadata["instruction_version"] == "1"
            assert (
                invocation.metadata["output_schema_fingerprint"]
                == invocation.instruction.output_contract.fingerprint
            )
            # The user prompt stays separate and no instruction text leaks into it.
            assert invocation.prompt == "top 10 order amounts in emea"
            assert DEFAULT_ROLE[:40] not in invocation.prompt

            record = store.get_record("wf-inv")
            assert record is not None
            assert DEFAULT_ROLE[:60] not in record.snapshot
            assert "top 10 order amounts" not in record.snapshot
        finally:
            store.close()


class TestEvaluationEvidence:
    async def test_evaluation_evidence_carries_instruction_identity(self) -> None:
        report = await run_evaluation()
        case = next(result for result in report.results if result.case_id == "normal-intent")
        assert case.evidence is not None
        assert case.evidence.instruction_fingerprint is not None
        assert case.evidence.output_schema_fingerprint is not None
        assert case.evidence.instruction_fingerprint.startswith("sha256:")
        assert case.evidence.output_schema_fingerprint == OutputContract().fingerprint

        query = next(
            case.request for case in build_ai_cases() if case.case_id == "normal-intent"
        )
        context = assemble_model_context(
            request=query,
            view=EVALUATION_VIEW,
            semantic_references=EVALUATION_REFERENCES,
        )
        bundle = assemble_instruction_bundle(
            request=query,
            context=context,
            view=EVALUATION_VIEW,
        )
        assert case.evidence.instruction_fingerprint == bundle.fingerprint
        assert evidence_is_redacted(case.evidence)

    async def test_evaluation_evidence_is_repeatable(self) -> None:
        first = await run_evaluation("run-ev-a")
        second = await run_evaluation("run-ev-a")
        first_evidence = next(
            result.evidence for result in first.results if result.case_id == "normal-intent"
        )
        second_evidence = next(
            result.evidence for result in second.results if result.case_id == "normal-intent"
        )
        assert first_evidence is not None and second_evidence is not None
        assert (
            first_evidence.instruction_fingerprint
            == second_evidence.instruction_fingerprint
        )
        assert (
            first_evidence.output_schema_fingerprint
            == second_evidence.output_schema_fingerprint
        )
        assert first.fingerprint == second.fingerprint

    async def test_no_raw_instruction_text_in_any_evidence(self) -> None:
        report = await run_evaluation()
        cases = {case.case_id: case for case in build_ai_cases()}
        for result in report.results:
            if result.evidence is None:
                continue
            payload = str(result.evidence.model_dump())
            assert DEFAULT_ROLE[:60] not in payload
            assert cases[result.case_id].request.prompt not in payload
