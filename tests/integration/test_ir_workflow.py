"""Durable workflow integration tests for the canonical IR (DDS-019).

Proves the runtime records the canonical IR version/fingerprint in
workflow compatibility evidence and safe stage metadata, rejects stale or
incompatible IR checkpoints on resume before adapter execution, and keeps
the P1/P2 fallback and legacy resume behavior intact through IR
normalization.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from nl2data import ErrorCode, OutcomeStatus, QueryContext, QueryRequest
from nl2data.errors import ErrorCategory, NL2DataError
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.adapters.sql.compile import compile_ir, compile_plan
from nl2data_core.ai.context import SemanticReference
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.ai.models import StructuredIntent
from nl2data_core.ai.plan_builder import build_plan_from_intent
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.fixtures import SQLiteFixtureProfile
from nl2data_core.governance.models import PolicyScope
from nl2data_core.planning.ir.compat import plan_to_ir
from nl2data_core.planning.models import ColumnBinding, PhysicalBinding
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.workflow.models import (
    WorkflowEvent,
    WorkflowStage,
    WorkflowState,
    WorkflowStatus,
)
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


class CountingAdapter(SqlQueryAdapter):
    """Adapter that counts executions (the external work boundary)."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.executions = 0

    async def execute(self, validated, context):
        self.executions += 1
        return await super().execute(validated, context)


class RetryableFailingAdapter(CountingAdapter):
    """An adapter that always fails with a retryable structured error."""

    async def execute(self, validated, context):
        self.executions += 1
        raise NL2DataError(
            ErrorCategory.ADAPTER,
            ErrorCode.SQL_EXECUTION_FAILED,
            "fixture adapter failure",
            retryable=True,
        )


class CountingIRCompiler:
    """IR compiler wrapper counting invocations (bound to the test binding)."""

    def __init__(self, binding: PhysicalBinding) -> None:
        self._binding = binding
        self.calls = 0

    def __call__(self, ir):
        self.calls += 1
        return compile_ir(ir, binding=self._binding)


class CountingPlanCompiler:
    """Legacy plan compiler wrapper counting invocations."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, plan):
        self.calls += 1
        return compile_plan(plan)


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


def make_adapter(tmp_path: Path, cls=CountingAdapter) -> CountingAdapter:
    return cls(
        dialect="sqlite",
        db_path=tmp_path / "fixture.db",
        allowed_objects=frozenset({"orders"}),
        allowed_columns=FIELDS,
        max_rows=100,
    )


def make_execution(
    tmp_path: Path, *, adapter: CountingAdapter, **overrides
) -> QueryExecutionRunner:
    fixture = SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")
    fixture.provision()
    values = {
        "adapter": adapter,
        "policy_scope": make_policy_scope(),
        "view": make_view(),
        "plan_resolver": StaticPlanResolver(None),
    }
    values.update(overrides)
    return QueryExecutionRunner(**values)


def make_runtime(
    tmp_path: Path,
    *,
    execution: QueryExecutionRunner,
    state_store: SQLiteStateStore | None = None,
    provider: FakeModelProvider | None = None,
    **overrides,
) -> DeterministicWorkflowRuntime:
    values = {
        "provider": provider or FakeModelProvider(default_response=VALID_INTENT),
        "execution": execution,
        "semantic_references": REFERENCES,
        "binding": BINDING,
    }
    if state_store is not None:
        values["state_store"] = state_store
    values.update(overrides)
    return DeterministicWorkflowRuntime(**values)


def request(request_id: str, workflow_id: str) -> QueryRequest:
    return QueryRequest(
        request_id=request_id,
        prompt="top 10 order amounts in emea",
        context=QueryContext(request_id=request_id, workflow_id=workflow_id),
    )


def make_checkpoint(
    store: SQLiteStateStore,
    *,
    workflow_id: str,
    request_id: str,
    status: WorkflowStatus = WorkflowStatus.RUNNING,
    **overrides,
) -> WorkflowState:
    values = {
        "workflow_id": workflow_id,
        "request_id": request_id,
        "status": status,
        "attempts": 1,
    }
    values.update(overrides)
    state = WorkflowState(**values)
    store.create(state)
    return state


def expected_ir(request_id: str):
    """The exact IR the runtime derives for a request on this fixture."""
    intent = StructuredIntent.model_validate(
        {
            **VALID_INTENT["intent"],
            "intent_id": f"intent-{request_id}",
            "request_id": request_id,
        }
    )
    plan = build_plan_from_intent(intent, binding=BINDING, catalog_fingerprint=None)
    return plan_to_ir(plan)


def ir_event(workflow_id: str, ir_version: str, ir_fingerprint: str) -> WorkflowEvent:
    """One checkpoint event carrying a canonical IR identity."""
    return WorkflowEvent(
        event_id=f"ev-{ir_fingerprint[-8:]}",
        workflow_id=workflow_id,
        from_status=WorkflowStatus.RUNNING,
        to_status=WorkflowStatus.RUNNING,
        occurred_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        metadata={"ir_version": ir_version, "ir_fingerprint": ir_fingerprint},
    )


class TestIrEvidence:
    async def test_durable_checkpoint_records_ir_evidence(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            adapter = make_adapter(tmp_path, RetryableFailingAdapter)
            runtime = make_runtime(
                tmp_path,
                execution=make_execution(tmp_path, adapter=adapter),
                state_store=store,
            )
            outcome = await runtime.execute(request("req-ir", "wf-ir"))
            assert outcome.status == OutcomeStatus.REJECTED
            assert outcome.error is not None
            assert outcome.error.code == ErrorCode.RETRY_EXHAUSTED

            expected = expected_ir("req-ir")
            checkpoint = store.get_checkpoint("wf-ir", "req-ir")
            assert checkpoint is not None
            #: The canonical IR identity participates in the compatibility evidence.
            assert checkpoint.compatibility_fingerprints["ir"] == sha256_fingerprint(
                {"ir_version": expected.ir_version, "ir_fingerprint": expected.fingerprint}
            )
            #: Stage events carry the IR version and fingerprint as safe metadata.
            identities = [
                event.metadata
                for event in checkpoint.events
                if "ir_version" in event.metadata or "ir_fingerprint" in event.metadata
            ]
            assert identities
            assert identities[-1]["ir_fingerprint"] == expected.fingerprint
            for event in checkpoint.events:
                if "ir_version" in event.metadata:
                    assert event.metadata["ir_version"] == "1"
            #: The persisted snapshot stays safe: no SQL and no IR payload.
            record = store.get_record("wf-ir")
            assert record is not None
            assert "SELECT" not in record.snapshot
            assert "required_capabilities" not in record.snapshot
        finally:
            store.close()

    async def test_durable_success_records_ir_evidence(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            adapter = make_adapter(tmp_path)
            runtime = make_runtime(
                tmp_path,
                execution=make_execution(tmp_path, adapter=adapter),
                state_store=store,
            )
            outcome = await runtime.execute(request("req-ok", "wf-ok"))
            assert outcome.status == OutcomeStatus.SUCCEEDED
            assert adapter.executions == 1
            checkpoint = store.get_checkpoint("wf-ok", "req-ok")
            assert checkpoint is not None
            assert "ir" in checkpoint.compatibility_fingerprints
        finally:
            store.close()


class TestCompilerSelection:
    async def test_ir_compiler_is_preferred_when_bound(self, tmp_path: Path) -> None:
        compiler = CountingIRCompiler(BINDING)
        runtime = make_runtime(
            tmp_path,
            execution=make_execution(tmp_path, adapter=make_adapter(tmp_path)),
            ir_compiler=compiler,
        )
        outcome = await runtime.execute(request("req-ir-c", "wf-ir-c"))
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert compiler.calls == 1

    async def test_plan_compiler_fallback_without_ir_compiler(self, tmp_path: Path) -> None:
        compiler = CountingPlanCompiler()
        runtime = make_runtime(
            tmp_path,
            execution=make_execution(tmp_path, adapter=make_adapter(tmp_path)),
            plan_compiler=compiler,
        )
        outcome = await runtime.execute(request("req-pc", "wf-pc"))
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert compiler.calls == 1


class TestStaleIrCheckpoint:
    async def test_stale_ir_checkpoint_rejected_before_adapter(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            old_fingerprint = "sha256:" + "aa" * 32
            make_checkpoint(
                store,
                workflow_id="wf-stale",
                request_id="req-stale",
                status=WorkflowStatus.RUNNING,
                current_stage=WorkflowStage.INTENT,
                events=(ir_event("wf-stale", "1", old_fingerprint),),
            )
            store.reserve_idempotency(
                "req-stale", request_id="req-stale", workflow_id="wf-stale"
            )
            adapter = make_adapter(tmp_path)
            runtime = make_runtime(
                tmp_path,
                execution=make_execution(tmp_path, adapter=adapter),
                state_store=store,
            )
            outcome = await runtime.execute(request("req-stale", "wf-stale"))
            assert outcome.status == OutcomeStatus.REJECTED
            assert outcome.error is not None
            assert outcome.error.code == ErrorCode.STALE_CHECKPOINT
            assert outcome.error.details["stored_ir_fingerprint"] == old_fingerprint
            assert outcome.error.details["current_ir_fingerprint"] == expected_ir(
                "req-stale"
            ).fingerprint
            #: Adapter execution never happened.
            assert adapter.executions == 0
        finally:
            store.close()

    async def test_partial_ir_identity_fails_closed(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            partial = WorkflowEvent(
                event_id="ev-partial",
                workflow_id="wf-partial",
                from_status=WorkflowStatus.RUNNING,
                to_status=WorkflowStatus.RUNNING,
                occurred_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
                metadata={"ir_version": "1"},  # fingerprint missing
            )
            make_checkpoint(
                store,
                workflow_id="wf-partial",
                request_id="req-partial",
                status=WorkflowStatus.RUNNING,
                current_stage=WorkflowStage.INTENT,
                events=(partial,),
            )
            store.reserve_idempotency(
                "req-partial", request_id="req-partial", workflow_id="wf-partial"
            )
            adapter = make_adapter(tmp_path)
            runtime = make_runtime(
                tmp_path,
                execution=make_execution(tmp_path, adapter=adapter),
                state_store=store,
            )
            outcome = await runtime.execute(request("req-partial", "wf-partial"))
            assert outcome.status == OutcomeStatus.REJECTED
            assert outcome.error is not None
            assert outcome.error.code == ErrorCode.STALE_CHECKPOINT
            assert adapter.executions == 0
        finally:
            store.close()

    async def test_matching_ir_checkpoint_resumes(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            expected = expected_ir("req-match")
            make_checkpoint(
                store,
                workflow_id="wf-match",
                request_id="req-match",
                status=WorkflowStatus.RUNNING,
                current_stage=WorkflowStage.INTENT,
                events=(ir_event("wf-match", str(expected.ir_version), expected.fingerprint),),
            )
            store.reserve_idempotency(
                "req-match", request_id="req-match", workflow_id="wf-match"
            )
            adapter = make_adapter(tmp_path)
            runtime = make_runtime(
                tmp_path,
                execution=make_execution(tmp_path, adapter=adapter),
                state_store=store,
            )
            outcome = await runtime.execute(request("req-match", "wf-match"))
            assert outcome.status == OutcomeStatus.SUCCEEDED
            assert adapter.executions == 1
        finally:
            store.close()

    async def test_legacy_checkpoint_without_ir_metadata_resumes(self, tmp_path: Path) -> None:
        """Checkpoints written before IR evidence existed resume untouched."""
        store = SQLiteStateStore(tmp_path / "durable.db")
        try:
            make_checkpoint(
                store,
                workflow_id="wf-legacy",
                request_id="req-legacy",
                status=WorkflowStatus.RUNNING,
                current_stage=WorkflowStage.INTENT,
            )
            store.reserve_idempotency(
                "req-legacy", request_id="req-legacy", workflow_id="wf-legacy"
            )
            adapter = make_adapter(tmp_path)
            runtime = make_runtime(
                tmp_path,
                execution=make_execution(tmp_path, adapter=adapter),
                state_store=store,
            )
            outcome = await runtime.execute(request("req-legacy", "wf-legacy"))
            assert outcome.status == OutcomeStatus.SUCCEEDED
            assert adapter.executions == 1
        finally:
            store.close()


class TestIrPreservation:
    async def test_not_configured_fallback_preserved(self, tmp_path: Path) -> None:
        runtime = DeterministicWorkflowRuntime(
            execution=make_execution(tmp_path, adapter=make_adapter(tmp_path)),
        )
        assert runtime.is_configured() is False
        outcome = await runtime.execute(request("req-nc", "wf-nc"))
        assert outcome.status == OutcomeStatus.NOT_CONFIGURED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.NOT_CONFIGURED
