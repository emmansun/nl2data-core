#!/usr/bin/env python3
"""Real-service mainflow demo: PostgreSQL source + durable state + Redis Memory.

This script demonstrates the canonical mainflow against a real PostgreSQL source
and durable workflow-state backend. Redis Memory is optional: the script
proceeds without it when Redis is unavailable but reports the capability gap.

Environment variables:
    NL2DATA_POSTGRES_DSN      PostgreSQL connection string for source and state.
    NL2DATA_REDIS_URL         Optional Redis URL for shared Memory.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import UTC, datetime

from nl2data import NL2Data, OutcomeStatus, QueryContext, QueryRequest
from nl2data.composition import CompositionProfile
from nl2data_core.ai.fake import FakeModelProvider
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

try:
    from nl2data_postgres.adapter import PostgresQueryAdapter
    from nl2data_postgres.config import PostgresAdapterConfig
except ImportError:  # pragma: no cover
    PostgresQueryAdapter = None  # type: ignore[misc,assignment]
    PostgresAdapterConfig = None  # type: ignore[misc,assignment]

try:
    from nl2data_workflow_postgres import PostgreSQLStateStore
except ImportError:  # pragma: no cover
    PostgreSQLStateStore = None  # type: ignore[misc,assignment]

try:
    from nl2data_memory_redis import RedisMemoryConfig, RedisMemoryProvider
except ImportError:  # pragma: no cover
    RedisMemoryConfig = None  # type: ignore[misc,assignment]
    RedisMemoryProvider = None

DEMO_PROMPT = "top 10 order amounts in emea"
FIELDS = frozenset({"order_id", "customer_id", "amount", "region", "status", "created_at"})

BINDING = PhysicalBinding(
    object_id="orders",
    dialect="postgres",
    column_bindings=(
        ColumnBinding(field_id="order_id", physical_name="order_id"),
        ColumnBinding(field_id="customer_id", physical_name="customer_id"),
        ColumnBinding(field_id="amount", physical_name="amount"),
        ColumnBinding(field_id="region", physical_name="region"),
        ColumnBinding(field_id="status", physical_name="status"),
        ColumnBinding(field_id="created_at", physical_name="created_at"),
    ),
)

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


def _check_prerequisites() -> tuple[str | None, str | None, bool]:
    dsn = os.environ.get("NL2DATA_POSTGRES_DSN")
    redis_url = os.environ.get("NL2DATA_REDIS_URL")
    ready = True
    if PostgresQueryAdapter is None or PostgresAdapterConfig is None:
        print("ERROR: nl2data-postgres package is not installed.")
        ready = False
    if PostgreSQLStateStore is None:
        print("ERROR: nl2data-workflow-postgres package is not installed.")
        ready = False
    if dsn is None:
        print("ERROR: NL2DATA_POSTGRES_DSN is not set.")
        ready = False
    return dsn, redis_url, ready


def _build_profile(*, dsn: str, redis_url: str | None) -> CompositionProfile:
    config = PostgresAdapterConfig(
        dsn_reference="dsn:" + dsn,
        allowed_objects=frozenset({"orders"}),
        allowed_fields=FIELDS,
        source_id="sales",
    )
    adapter = PostgresQueryAdapter(
        config,
        allowed_objects=frozenset({"orders"}),
        allowed_columns=FIELDS,
    )
    state_store = PostgreSQLStateStore(dsn=dsn)

    memory = None
    if redis_url and RedisMemoryProvider is not None and RedisMemoryConfig is not None:
        candidate = RedisMemoryProvider(RedisMemoryConfig(namespace="demo"), url=redis_url)
        if candidate.is_available():
            memory = candidate
        else:
            print("Redis memory unavailable; proceeding without shared Memory.")

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

    return CompositionProfile(
        provider=FakeModelProvider(default_response=STATIC_INTENT),
        adapter=adapter,
        policy_scope=policy_scope,
        view=view,
        plan_resolver=StaticPlanResolver(IR),
        binding=BINDING,
        state_store=state_store,
        memory=memory,
    )


async def run_demo(*, dsn: str, redis_url: str | None, run_id: str | None = None) -> bool:
    """Run the real-service mainflow demo against a persistent database.

    ``run_id`` disambiguates the workflow/request identifiers so repeated runs
    never collide with records left by earlier runs; it defaults to a fresh
    timestamp suffix.
    """
    suffix = run_id or datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    request_id = f"demo-req-{suffix}"
    workflow_id = f"demo-wf-{suffix}"
    cancel_request_id = f"demo-req-cancel-{suffix}"
    cancel_workflow_id = f"demo-wf-cancel-{suffix}"

    profile = _build_profile(dsn=dsn, redis_url=redis_url)
    facade = NL2Data(composition=profile)
    print("facade lifecycle:", facade.lifecycle.value)
    await facade.initialize()
    print("facade initialized; health:", facade.health().status.value)

    capabilities = facade.capabilities()
    print("facade configured:", capabilities.configured)
    print("facade durable_state:", capabilities.durable_state)
    print("facade memory:", capabilities.memory)

    request = QueryRequest(
        request_id=request_id,
        prompt=DEMO_PROMPT,
        context=QueryContext(request_id=request_id, workflow_id=workflow_id),
    )
    outcome = await facade.aquery(request)
    print("first outcome status:", outcome.status.value)
    assert outcome.status == OutcomeStatus.SUCCEEDED, outcome.error
    assert outcome.result is not None
    print("result rows:", len(outcome.result.rows))

    replay = await facade.aquery(request)
    print("replay outcome status:", replay.status.value)
    assert replay.status == OutcomeStatus.REJECTED
    assert replay.error is not None
    assert replay.error.code == "DUPLICATE_REQUEST"

    handle = facade.get_workflow(workflow_id)
    print("workflow handle:", handle is not None)
    assert handle is not None
    assert handle.status.value == "succeeded"

    # Cancellation fail-fast: a cancelled non-terminal workflow must fail
    # before any adapter execution.
    state_store = PostgreSQLStateStore(dsn=dsn)
    state_store.create(
        WorkflowState(
            workflow_id=cancel_workflow_id,
            request_id=cancel_request_id,
            status=WorkflowStatus.RUNNING,
            cancellation_requested=True,
        )
    )
    cancel_request = QueryRequest(
        request_id=cancel_request_id,
        prompt=DEMO_PROMPT,
        context=QueryContext(
            request_id=cancel_request_id, workflow_id=cancel_workflow_id
        ),
    )
    cancel_outcome = await facade.aquery(cancel_request)
    print("cancelled outcome status:", cancel_outcome.status.value)
    assert cancel_outcome.status == OutcomeStatus.REJECTED
    assert cancel_outcome.error is not None
    assert cancel_outcome.error.code == "WORKFLOW_CANCELLED"

    await facade.close()
    print("facade closed")

    # Recovery across a new process: rebuild the runtime and replay.
    profile2 = _build_profile(dsn=dsn, redis_url=redis_url)
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
    parser = argparse.ArgumentParser(description="Run the real-service mainflow demo.")
    parser.add_argument(
        "--dsn",
        default=None,
        help="PostgreSQL DSN (defaults to NL2DATA_POSTGRES_DSN).",
    )
    parser.add_argument(
        "--redis-url",
        default=None,
        help="Redis URL (defaults to NL2DATA_REDIS_URL).",
    )
    args = parser.parse_args()

    dsn, redis_url, ready = _check_prerequisites()
    if args.dsn:
        dsn = args.dsn
    if args.redis_url is not None:
        redis_url = args.redis_url

    if not ready or dsn is None:
        print("\nReal-service demo prerequisites are not satisfied.")
        print("Set NL2DATA_POSTGRES_DSN and install the optional backend packages.")
        return 1

    try:
        passed = asyncio.run(run_demo(dsn=dsn, redis_url=redis_url))
    except Exception as exc:  # pragma: no cover
        print("demo failed:", exc)
        return 1

    if passed:
        print("\nReal-service mainflow demo passed.")
        return 0
    return 1  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
