#!/usr/bin/env python3
"""Deterministic mainflow demo: no external services required.

This script exercises the public NL2Data facade through a complete lifecycle:
configuration -> initialize -> execute -> durable persist/recover.  It uses a
SQLite fixture as the source database, a fake model provider, and a SQLite
durable state store.
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from pathlib import Path

from nl2data import NL2Data, OutcomeStatus, QueryContext, QueryRequest
from nl2data.composition import CompositionProfile
from nl2data_core.adapters.sql.adapter import SqlQueryAdapter
from nl2data_core.ai.fake import FakeModelProvider
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
from nl2data_core.workflow.models import WorkflowState, WorkflowStatus
from nl2data_core.workflow.runner import StaticPlanResolver
from nl2data_core.workflow.sqlite_store import SQLiteStateStore

DEMO_PROMPT = "top 10 order amounts in emea"
FIELDS = frozenset({"order_id", "customer_id", "amount", "region", "status", "created_at"})

#: Bounded physical binding for the SQLite fixture.
BINDING = PhysicalBinding(
    object_id="orders",
    dialect="sqlite",
    column_bindings=(
        ColumnBinding(field_id="order_id", physical_name="order_id"),
        ColumnBinding(field_id="customer_id", physical_name="customer_id"),
        ColumnBinding(field_id="amount", physical_name="amount"),
        ColumnBinding(field_id="region", physical_name="region"),
        ColumnBinding(field_id="status", physical_name="status"),
        ColumnBinding(field_id="created_at", physical_name="created_at"),
    ),
)

#: Static intent returned by the fake provider for every demo request.
STATIC_INTENT = {
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

#: Bounded semantic IR produced by the static plan resolver.
IR = SemanticQueryIR(
    ir_id="demo-ir",
    source_id="sales",
    root_entity_id="order",
    selections=(
        IRSelection(selection_id="s1", field_id="order_id", alias="oid"),
        IRSelection(selection_id="s2", field_id="amount", alias="amt"),
    ),
    filters=(
        IRFilter(filter_id="f1", field_id="region", operator="eq", value="emea"),
    ),
    orderings=(IROrdering(ordering_id="o1", field_id="order_id", direction="desc"),),
    limit=10,
    provenance=IRProvenance(source_id="sales", root_entity_id="order"),
)


async def run_demo(*, db_dir: Path) -> bool:
    """Run the deterministic demo and return whether all checkpoints passed."""
    fixture = SQLiteFixtureProfile(db_path=db_dir / "source.db")
    fixture.provision()

    state_store = SQLiteStateStore(db_dir / "durable.db")
    adapter = SqlQueryAdapter(
        dialect="sqlite",
        db_path=fixture.db_path,
        allowed_objects=frozenset({"orders"}),
        allowed_columns=FIELDS,
        max_rows=100,
    )
    policy_scope = PolicyScope(
        policy_id="demo-policy",
        source_ids=frozenset({"sales"}),
        resource_ids=frozenset({"orders"}),
        operation_ids=frozenset({"select"}),
        field_ids=FIELDS,
    )
    view = AuthorizedView(
        source_id="sales",
        root_entity_ids=frozenset({"order"}),
        field_ids=FIELDS,
    )

    profile = CompositionProfile(
        provider=FakeModelProvider(default_response=STATIC_INTENT),
        adapter=adapter,
        policy_scope=policy_scope,
        view=view,
        plan_resolver=StaticPlanResolver(IR),
        binding=BINDING,
        state_store=state_store,
    )

    facade = NL2Data(composition=profile)
    print("facade lifecycle:", facade.lifecycle.value)
    await facade.initialize()
    print("facade initialized; health:", facade.health().status.value)

    capabilities = facade.capabilities()
    print("facade configured:", capabilities.configured)
    print("facade durable_state:", capabilities.durable_state)

    request = QueryRequest(
        request_id="demo-req-1",
        prompt=DEMO_PROMPT,
        context=QueryContext(request_id="demo-req-1", workflow_id="demo-wf-1"),
    )
    outcome = await facade.aquery(request)
    print("first outcome status:", outcome.status.value)
    assert outcome.status == OutcomeStatus.SUCCEEDED, outcome.error
    assert outcome.result is not None
    print("result rows:", len(outcome.result.rows))

    # Durable recovery: duplicate request replays without re-executing adapter.
    replay = await facade.aquery(request)
    print("replay outcome status:", replay.status.value)
    assert replay.status == OutcomeStatus.REJECTED
    assert replay.error is not None
    assert replay.error.code == "DUPLICATE_REQUEST"

    # Workflow handle lookup.
    handle = facade.get_workflow("demo-wf-1")
    print("workflow handle:", handle is not None)
    assert handle is not None
    assert handle.status.value == "succeeded"

    # Cancellation fail-fast: a non-terminal workflow flagged as cancelled
    # must fail before any adapter execution.
    state_store.create(
        WorkflowState(
            workflow_id="demo-wf-cancel",
            request_id="demo-req-cancel",
            status=WorkflowStatus.RUNNING,
            cancellation_requested=True,
        )
    )
    cancel_request = QueryRequest(
        request_id="demo-req-cancel",
        prompt=DEMO_PROMPT,
        context=QueryContext(request_id="demo-req-cancel", workflow_id="demo-wf-cancel"),
    )
    cancel_outcome = await facade.aquery(cancel_request)
    print("cancelled outcome status:", cancel_outcome.status.value)
    assert cancel_outcome.status == OutcomeStatus.REJECTED
    assert cancel_outcome.error is not None
    assert cancel_outcome.error.code == "WORKFLOW_CANCELLED"

    await facade.close()
    print("facade closed")

    # Recovery across process boundary: reopen the state store and replay.
    state_store2 = SQLiteStateStore(db_dir / "durable.db")
    profile2 = CompositionProfile(
        provider=FakeModelProvider(default_response=STATIC_INTENT),
        adapter=adapter,
        policy_scope=policy_scope,
        view=view,
        plan_resolver=StaticPlanResolver(IR),
        binding=BINDING,
        state_store=state_store2,
    )
    facade2 = NL2Data(composition=profile2)
    await facade2.initialize()
    recovery = await facade2.aquery(request)
    print("recovery outcome status:", recovery.status.value)
    assert recovery.status == OutcomeStatus.REJECTED
    assert recovery.error is not None
    assert recovery.error.code == "DUPLICATE_REQUEST"
    await facade2.close()

    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic mainflow demo.")
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Directory for the SQLite source and durable state files.",
    )
    args = parser.parse_args()

    work_dir = args.work_dir or Path(tempfile.mkdtemp(prefix="nl2data-demo-"))
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        passed = asyncio.run(run_demo(db_dir=work_dir))
    except Exception as exc:  # pragma: no cover
        print("demo failed:", exc)
        return 1

    if passed:
        print("\nDeterministic mainflow demo passed.")
        return 0
    return 1  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
