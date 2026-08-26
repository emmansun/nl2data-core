"""Real-PostgreSQL integration profile for nl2data-workflow-postgres.

Proves schema bootstrap/versioning, state compare-and-set, atomic
idempotency, lease lifecycle with takeover, fencing, and bounded cleanup
are durable across connections.  When the driver is missing, the DSN is not
configured, or the service is unreachable the outcome is skipped - never a
pass.  Every run uses a unique schema namespace with best-effort cleanup.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from nl2data_core.workflow.durable import IdempotencyConflictError, IdempotencyStatus
from nl2data_core.workflow.models import (
    WorkflowState,
    WorkflowStateError,
    WorkflowStatus,
)
from nl2data_core.workflow.shared_errors import SharedStoreError, SharedStoreErrorCode
from nl2data_core.workflow.transitions import transition

from nl2data_workflow_postgres import (
    PostgreSQLStateStore,
    WorkflowPostgresConfig,
    build_pool,
    driver_available,
)

DSN = os.environ.get(
    "NL2DATA_POSTGRES_DSN", "postgres://postgres:postgres@127.0.0.1:5432/postgres"
)

SCOPE_A = "sha256:" + "a" * 64
SCOPE_B = "sha256:" + "b" * 64
FINGERPRINT = "sha256:" + "f" * 64

TTL_SECONDS = 2.0
TAKEOVER_WAIT_SECONDS = TTL_SECONDS + 0.6


def make_state(
    workflow_id: str = "wf-1",
    request_id: str = "req-1",
    status: WorkflowStatus = WorkflowStatus.CREATED,
    scope: str | None = None,
) -> WorkflowState:
    return WorkflowState(
        workflow_id=workflow_id,
        request_id=request_id,
        status=status,
        tenant_scope_fingerprint=scope,
    )


def make_terminal(workflow_id: str, scope: str | None = None) -> WorkflowState:
    state = make_state(workflow_id=workflow_id, scope=scope)
    for target in (WorkflowStatus.QUEUED, WorkflowStatus.RUNNING, WorkflowStatus.SUCCEEDED):
        state = transition(state, target, event_id=f"ev-{uuid4().hex[:8]}")
    return state


@pytest.fixture(scope="module")
def shared_backend() -> Iterator[tuple[PostgreSQLStateStore, Any]]:
    """A real store over a unique schema namespace; skipped when unavailable."""
    if not driver_available():
        pytest.skip("the psycopg driver is not installed; the real postgres profile is skipped")
    namespace = f"workflow_{uuid4().hex[:10]}"
    pool = build_pool(
        DSN,
        pool_size=2,
        connect_timeout_seconds=2.0,
        command_timeout_seconds=10.0,
        acquire_timeout_seconds=2.0,
        schema=namespace,
    )
    try:
        with pool.connection() as conn:
            conn.execute("SELECT 1")
            conn.commit()
    except Exception:
        with contextlib.suppress(Exception):
            pool.close()
        pytest.skip("postgres service is unavailable; the real postgres profile is skipped")
    store = PostgreSQLStateStore(
        pool=pool,
        config=WorkflowPostgresConfig(
            namespace=namespace, clock_tolerance_seconds=0.5
        ),
    )
    try:
        yield store, pool
    finally:
        with contextlib.suppress(Exception), pool.connection() as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{namespace}" CASCADE')
            conn.commit()
        with contextlib.suppress(Exception):
            pool.close()


class TestSchemaBootstrap:
    def test_schema_initializes_once_and_reopens(
        self, shared_backend: tuple[PostgreSQLStateStore, Any]
    ) -> None:
        store, pool = shared_backend
        assert store.schema_version() == 1
        reopened = PostgreSQLStateStore(
            pool=pool,
            config=WorkflowPostgresConfig(
                namespace=store.schema, clock_tolerance_seconds=0.5
            ),
        )
        assert reopened.schema_version() == 1


class TestStateRoundTrip:
    def test_create_get_and_compare_and_set(
        self, shared_backend: tuple[PostgreSQLStateStore, Any]
    ) -> None:
        store, _ = shared_backend
        store.create(make_state(scope=SCOPE_A))
        assert store.get("wf-1", tenant_scope_fingerprint=SCOPE_A) == make_state(
            scope=SCOPE_A
        )
        next_state = transition(
            make_state(scope=SCOPE_A), WorkflowStatus.QUEUED, event_id="ev-1"
        )
        store.update(
            "wf-1",
            WorkflowStatus.CREATED,
            next_state,
            expected_version=1,
            tenant_scope_fingerprint=SCOPE_A,
        )
        with pytest.raises(WorkflowStateError) as excinfo:
            store.update(
                "wf-1",
                WorkflowStatus.QUEUED,
                next_state,
                expected_version=1,
                tenant_scope_fingerprint=SCOPE_A,
            )
        assert "version changed concurrently" in str(excinfo.value)

    def test_tenant_scopes_stay_isolated(
        self, shared_backend: tuple[PostgreSQLStateStore, Any]
    ) -> None:
        store, _ = shared_backend
        store.create(make_state(workflow_id="wf-a", scope=SCOPE_B))
        assert store.get("wf-a", tenant_scope_fingerprint=SCOPE_B) is not None
        assert store.list_ids(tenant_scope_fingerprint=SCOPE_B) == ("wf-a",)
        assert store.get("wf-a", tenant_scope_fingerprint=SCOPE_A) is None


class TestIdempotency:
    def test_reserve_complete_and_replay(
        self, shared_backend: tuple[PostgreSQLStateStore, Any]
    ) -> None:
        store, _ = shared_backend
        reserved = store.reserve_idempotency(
            "key-1",
            request_id="req-1",
            workflow_id="wf-1",
            tenant_scope_fingerprint=SCOPE_A,
        )
        assert reserved.status is IdempotencyStatus.RESERVED
        completed = store.complete_idempotency(
            "key-1",
            workflow_id="wf-1",
            terminal_outcome_fingerprint=FINGERPRINT,
            tenant_scope_fingerprint=SCOPE_A,
        )
        assert completed.status is IdempotencyStatus.COMPLETED
        replay = store.get_idempotency("key-1", tenant_scope_fingerprint=SCOPE_A)
        assert replay is not None
        assert replay.status is IdempotencyStatus.COMPLETED
        assert replay.terminal_outcome_fingerprint == FINGERPRINT
        stable = store.reserve_idempotency(
            "key-1",
            request_id="req-1",
            workflow_id="wf-1",
            tenant_scope_fingerprint=SCOPE_A,
        )
        assert stable.status is IdempotencyStatus.COMPLETED

    def test_conflicting_reuse_is_rejected(
        self, shared_backend: tuple[PostgreSQLStateStore, Any]
    ) -> None:
        store, _ = shared_backend
        store.reserve_idempotency(
            "key-2",
            request_id="req-1",
            workflow_id="wf-1",
            tenant_scope_fingerprint=SCOPE_A,
        )
        with pytest.raises(IdempotencyConflictError):
            store.reserve_idempotency(
                "key-2",
                request_id="req-other",
                workflow_id="wf-other",
                tenant_scope_fingerprint=SCOPE_A,
            )


class TestLeaseAndFencing:
    def test_lease_lifecycle(
        self, shared_backend: tuple[PostgreSQLStateStore, Any]
    ) -> None:
        store, _ = shared_backend
        lease = store.acquire_lease(
            "wf-lease",
            owner_id="worker-a",
            tenant_scope_fingerprint=SCOPE_A,
            ttl_seconds=TTL_SECONDS,
        )
        assert lease.fencing_token == 1
        assert lease.valid(now=datetime.now(UTC)) is True
        renewed = store.renew_lease(
            "wf-lease",
            owner_id="worker-a",
            fencing_token=lease.fencing_token,
            tenant_scope_fingerprint=SCOPE_A,
        )
        assert renewed.expires_at > lease.expires_at
        assert (
            store.release_lease(
                "wf-lease",
                owner_id="worker-a",
                fencing_token=renewed.fencing_token,
                tenant_scope_fingerprint=SCOPE_A,
            )
            is True
        )
        assert store.inspect_lease("wf-lease", tenant_scope_fingerprint=SCOPE_A) is None

    def test_takeover_fences_stale_worker(
        self, shared_backend: tuple[PostgreSQLStateStore, Any]
    ) -> None:
        store, _ = shared_backend
        store.create(make_state(workflow_id="wf-race", scope=SCOPE_A))
        old = store.acquire_lease(
            "wf-race",
            owner_id="worker-a",
            tenant_scope_fingerprint=SCOPE_A,
            ttl_seconds=TTL_SECONDS,
        )
        time.sleep(TAKEOVER_WAIT_SECONDS)
        new = store.acquire_lease(
            "wf-race",
            owner_id="worker-b",
            tenant_scope_fingerprint=SCOPE_A,
            ttl_seconds=TTL_SECONDS,
        )
        assert new.fencing_token > old.fencing_token
        with pytest.raises(SharedStoreError) as excinfo:
            store.update(
                "wf-race",
                WorkflowStatus.CREATED,
                transition(
                    make_state(workflow_id="wf-race", scope=SCOPE_A),
                    WorkflowStatus.QUEUED,
                    event_id="ev-a",
                ),
                expected_version=1,
                tenant_scope_fingerprint=SCOPE_A,
                owner_id="worker-a",
                fencing_token=old.fencing_token,
            )
        assert excinfo.value.code is SharedStoreErrorCode.FENCING_REJECTED
        store.update(
            "wf-race",
            WorkflowStatus.CREATED,
            transition(
                make_state(workflow_id="wf-race", scope=SCOPE_A),
                WorkflowStatus.QUEUED,
                event_id="ev-b",
            ),
            expected_version=1,
            tenant_scope_fingerprint=SCOPE_A,
            owner_id="worker-b",
            fencing_token=new.fencing_token,
        )
        stored = store.get("wf-race", tenant_scope_fingerprint=SCOPE_A)
        assert stored is not None and stored.status is WorkflowStatus.QUEUED


class TestCleanup:
    def test_cleanup_removes_only_terminal_and_expired(
        self, shared_backend: tuple[PostgreSQLStateStore, Any]
    ) -> None:
        store, _ = shared_backend
        store.create(make_state(workflow_id="wf-done", scope=SCOPE_A))
        store.update(
            "wf-done",
            WorkflowStatus.CREATED,
            make_terminal("wf-done", scope=SCOPE_A),
            expected_version=1,
            tenant_scope_fingerprint=SCOPE_A,
        )
        store.create(make_state(workflow_id="wf-live", scope=SCOPE_A))
        store.update(
            "wf-live",
            WorkflowStatus.CREATED,
            transition(
                make_state(workflow_id="wf-live", scope=SCOPE_A),
                WorkflowStatus.QUEUED,
                event_id="ev-live",
            ),
            expected_version=1,
            tenant_scope_fingerprint=SCOPE_A,
        )
        store.reserve_idempotency(
            "key-stale",
            request_id="req-stale",
            workflow_id="wf-live",
            tenant_scope_fingerprint=SCOPE_A,
            expires_at=datetime.now(UTC) - timedelta(seconds=10),
        )
        store.reserve_idempotency(
            "key-fresh",
            request_id="req-fresh",
            workflow_id="wf-live",
            tenant_scope_fingerprint=SCOPE_A,
        )
        store.acquire_lease(
            "wf-stale-lease",
            owner_id="worker-stale",
            tenant_scope_fingerprint=SCOPE_A,
            ttl_seconds=TTL_SECONDS,
        )
        store.acquire_lease(
            "wf-live",
            owner_id="worker-live",
            tenant_scope_fingerprint=SCOPE_A,
            ttl_seconds=TTL_SECONDS * 10,
        )
        time.sleep(TAKEOVER_WAIT_SECONDS)
        removed = store.cleanup(
            terminal_before=datetime.now(UTC),
            expired_before=datetime.now(UTC),
            max_records=100,
        )
        assert removed >= 3
        assert store.get("wf-done", tenant_scope_fingerprint=SCOPE_A) is None
        assert store.get("wf-live", tenant_scope_fingerprint=SCOPE_A) is not None
        assert store.get_idempotency("key-stale", tenant_scope_fingerprint=SCOPE_A) is None
        assert store.get_idempotency("key-fresh", tenant_scope_fingerprint=SCOPE_A) is not None
        assert store.inspect_lease("wf-stale-lease", tenant_scope_fingerprint=SCOPE_A) is None
        assert store.inspect_lease("wf-live", tenant_scope_fingerprint=SCOPE_A) is not None
