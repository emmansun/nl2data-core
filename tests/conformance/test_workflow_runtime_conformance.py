"""Workflow-runtime conformance cases (P2.5).

Proves the deterministic governed runtime end to end against the controlled
SQLite fixture: fixed repeatable results, protected public outcomes, and the
mandatory gate order that stops pre-execution branches before the adapter.
A future optional backend must satisfy the same expectations; the reference
runtime is the conformance baseline.  Nothing depends on wall-clock time or
random state.
"""

from __future__ import annotations

import re
from pathlib import Path

from nl2data import ErrorCode, OutcomeStatus, QueryContext, QueryRequest
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.ai.context import SemanticReference
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.fixtures import SQLiteFixtureProfile
from nl2data_core.governance.models import PolicyScope
from nl2data_core.planning.models import ColumnBinding, PhysicalBinding
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.workflow.runner import QueryExecutionRunner, StaticPlanResolver
from nl2data_core.workflow.runtime import DeterministicWorkflowRuntime

FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
FIELDS = frozenset({"order_id", "customer_id", "amount", "region", "status", "created_at"})
TOP10 = (
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


def make_adapter(tmp_path: Path, **overrides) -> CountingAdapter:
    values = {
        "dialect": "sqlite",
        "db_path": tmp_path / "fixture.db",
        "allowed_objects": frozenset({"orders"}),
        "allowed_columns": FIELDS,
        "max_rows": 100,
    }
    values.update(overrides)
    return CountingAdapter(**values)


def make_runtime(
    tmp_path: Path,
    *,
    adapter: CountingAdapter | None = None,
    provider: FakeModelProvider | None = None,
    policy_scope: PolicyScope | None = None,
) -> DeterministicWorkflowRuntime:
    fixture = SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")
    fixture.provision()
    execution = QueryExecutionRunner(
        adapter=adapter or make_adapter(tmp_path),
        policy_scope=policy_scope or make_policy_scope(),
        view=make_view(),
        plan_resolver=StaticPlanResolver(None),
    )
    return DeterministicWorkflowRuntime(
        provider=provider or FakeModelProvider(default_response=VALID_INTENT),
        execution=execution,
        semantic_references=REFERENCES,
        binding=BINDING,
    )


def request(request_id: str = "conformance-1") -> QueryRequest:
    return QueryRequest(
        request_id=request_id,
        prompt="top 10 order amounts in emea",
        context=QueryContext(request_id=request_id, workflow_id=f"wf-{request_id}"),
    )


class TestRuntimeReadFlow:
    async def test_runtime_read_matches_fixed_expectations(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path)
        runtime = make_runtime(tmp_path, adapter=adapter)
        outcome = await runtime.execute(request())
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert outcome.error is None
        assert outcome.result is not None
        assert outcome.result.column_names == ("order_id", "amount")
        assert outcome.result.rows == TOP10
        assert FINGERPRINT.fullmatch(outcome.result.fingerprint)
        assert adapter.executions == 1

    async def test_runtime_results_are_repeatable(self, tmp_path: Path) -> None:
        runtime = make_runtime(tmp_path)
        first = await runtime.execute(request("conformance-1"))
        second = await runtime.execute(request("conformance-2"))
        assert first.status == OutcomeStatus.SUCCEEDED
        assert first.result is not None and second.result is not None
        assert first.result.fingerprint == second.result.fingerprint
        assert first.result.rows == second.result.rows


class TestProtectedOutcome:
    async def test_outcome_carries_only_protected_scalars(self, tmp_path: Path) -> None:
        outcome = await make_runtime(tmp_path).execute(request())
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert outcome.result is not None
        for row in outcome.result.rows:
            assert all(isinstance(cell, (str, int, float, bool, type(None))) for cell in row)
        # The raw prompt never reaches the outcome or its error details.
        payload = str(outcome.model_dump())
        assert "top 10" not in payload
        assert "SELECT" not in payload

    async def test_workflow_identity_propagates_to_the_outcome(
        self, tmp_path: Path
    ) -> None:
        outcome = await make_runtime(tmp_path).execute(request("conformance-3"))
        assert outcome.status == OutcomeStatus.SUCCEEDED
        assert outcome.workflow_id == "wf-conformance-3"


class TestMandatoryGates:
    async def test_governance_denial_stops_before_adapter(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path)
        denied = make_policy_scope(field_ids=FIELDS - {"amount"})
        runtime = make_runtime(tmp_path, adapter=adapter, policy_scope=denied)
        outcome = await runtime.execute(request())
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.result is None
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.GOVERNANCE_DENIED
        assert adapter.executions == 0  # the gate order is absolute

    async def test_malformed_intent_stops_before_adapter(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path)
        provider = FakeModelProvider(default_response={"intent": {"selections": "broken"}})
        runtime = make_runtime(tmp_path, adapter=adapter, provider=provider)
        outcome = await runtime.execute(request())
        assert outcome.status == OutcomeStatus.REJECTED
        assert outcome.error is not None
        assert outcome.error.code == ErrorCode.MODEL_INVOCATION_FAILED
        assert outcome.error.details["model_code"] == "MALFORMED_RESPONSE"
        assert adapter.executions == 0
