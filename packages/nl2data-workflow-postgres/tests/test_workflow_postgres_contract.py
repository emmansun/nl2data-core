"""Shared-store contract tests for nl2data-workflow-postgres.

Verifies the replaceable shared backend contract over the deterministic
in-memory fake pool: cross-instance visibility, transactional
compare-and-set, atomic idempotency, lease lifecycle, stale-owner takeover,
and fencing.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from nl2data_core.workflow.durable import (
    IdempotencyConflictError,
    IdempotencyStatus,
)
from nl2data_core.workflow.models import (
    WorkflowState,
    WorkflowStateError,
    WorkflowStatus,
    WorkflowTransitionError,
)
from nl2data_core.workflow.shared_errors import SharedStoreError, SharedStoreErrorCode
from nl2data_core.workflow.transitions import transition

from nl2data_workflow_postgres import PostgreSQLStateStore
from nl2data_workflow_postgres.fake_postgres import FakePostgresPool

SCOPE_A = "sha256:" + "a" * 64
SCOPE_B = "sha256:" + "b" * 64
FINGERPRINT = "sha256:" + "f" * 64


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


def make_store() -> tuple[PostgreSQLStateStore, FakePostgresPool]:
    pool = FakePostgresPool()
    store = PostgreSQLStateStore(pool=pool, now=pool.clock.now)
    return store, pool


def queued(state: WorkflowState) -> WorkflowState:
    return transition(state, WorkflowStatus.QUEUED, event_id="ev-queued")


class TestCrossInstanceVisibility:
    def test_state_stored_by_worker_a_is_visible_to_worker_b(self) -> None:
        pool = FakePostgresPool()
        store_a = PostgreSQLStateStore(pool=pool, now=pool.clock.now)
        store_b = PostgreSQLStateStore(pool=pool, now=pool.clock.now)
        state = make_state(scope=SCOPE_A)
        store_a.create(state)
        assert store_b.get("wf-1", tenant_scope_fingerprint=SCOPE_A) == state

    def test_cross_tenant_lookup_returns_nothing(self) -> None:
        store, _ = make_store()
        store.create(make_state(scope=SCOPE_A))
        assert store.get("wf-1", tenant_scope_fingerprint=SCOPE_B) is None
        assert store.get("wf-1") is None

    def test_checkpoint_lookup_requires_matching_request(self) -> None:
        store, _ = make_store()
        store.create(make_state(scope=SCOPE_A))
        assert (
            store.get_checkpoint("wf-1", "req-1", tenant_scope_fingerprint=SCOPE_A)
            is not None
        )
        assert (
            store.get_checkpoint("wf-1", "other-request", tenant_scope_fingerprint=SCOPE_A)
            is None
        )

    def test_list_ids_is_scope_bounded(self) -> None:
        store, _ = make_store()
        store.create(make_state(workflow_id="wf-a", scope=SCOPE_A))
        store.create(make_state(workflow_id="wf-b", scope=SCOPE_B))
        store.create(make_state(workflow_id="wf-local"))
        assert store.list_ids(tenant_scope_fingerprint=SCOPE_A) == ("wf-a",)
        assert store.list_ids(tenant_scope_fingerprint=SCOPE_B) == ("wf-b",)
        assert store.list_ids() == ("wf-local",)

    def test_idempotency_record_is_visible_across_instances(self) -> None:
        pool = FakePostgresPool()
        store_a = PostgreSQLStateStore(pool=pool, now=pool.clock.now)
        store_b = PostgreSQLStateStore(pool=pool, now=pool.clock.now)
        store_a.reserve_idempotency(
            "key-1",
            request_id="req-1",
            workflow_id="wf-1",
            tenant_scope_fingerprint=SCOPE_A,
        )
        record = store_b.get_idempotency("key-1", tenant_scope_fingerprint=SCOPE_A)
        assert record is not None
        assert record.status is IdempotencyStatus.RESERVED


class TestCompareAndSet:
    def test_concurrent_update_from_same_revision_has_one_winner(self) -> None:
        store, _ = make_store()
        store.create(make_state())
        winner = queued(make_state())
        store.update("wf-1", WorkflowStatus.CREATED, winner, expected_version=1)
        loser = queued(make_state(request_id="req-other"))
        with pytest.raises(WorkflowStateError):
            store.update("wf-1", WorkflowStatus.CREATED, loser, expected_version=1)
        stored = store.get("wf-1")
        assert stored is not None and stored.request_id == "req-1"

    def test_stale_expected_version_is_rejected(self) -> None:
        store, _ = make_store()
        store.create(make_state())
        store.update("wf-1", WorkflowStatus.CREATED, queued(make_state()), expected_version=1)
        with pytest.raises(WorkflowStateError) as excinfo:
            store.update(
                "wf-1", WorkflowStatus.QUEUED, queued(make_state()), expected_version=1
            )
        assert "version changed concurrently" in str(excinfo.value)

    def test_stale_expected_status_is_rejected(self) -> None:
        store, _ = make_store()
        store.create(make_state())
        with pytest.raises(WorkflowStateError) as excinfo:
            store.update("wf-1", WorkflowStatus.RUNNING, queued(make_state()))
        assert "status changed concurrently" in str(excinfo.value)

    def test_terminal_status_is_never_overwritten(self) -> None:
        store, _ = make_store()
        store.create(make_state(status=WorkflowStatus.SUCCEEDED))
        with pytest.raises(WorkflowTransitionError):
            store.update(
                "wf-1",
                WorkflowStatus.SUCCEEDED,
                make_state(status=WorkflowStatus.QUEUED),
            )

    def test_update_of_missing_workflow_is_rejected(self) -> None:
        store, _ = make_store()
        with pytest.raises(WorkflowStateError) as excinfo:
            store.update(
                "wf-missing",
                WorkflowStatus.CREATED,
                make_state(workflow_id="wf-missing"),
            )
        assert "not found" in str(excinfo.value)

    def test_update_with_mismatched_scope_is_rejected(self) -> None:
        store, _ = make_store()
        store.create(make_state(scope=SCOPE_A))
        with pytest.raises(WorkflowStateError) as excinfo:
            store.update(
                "wf-1",
                WorkflowStatus.QUEUED,
                make_state(scope=SCOPE_B),
                expected_version=1,
                tenant_scope_fingerprint=SCOPE_A,
            )
        assert "tenant scope mismatch" in str(excinfo.value)
        stored = store.get("wf-1", tenant_scope_fingerprint=SCOPE_A)
        assert stored is not None and stored.tenant_scope_fingerprint == SCOPE_A

    def test_fenced_update_requires_current_owner_and_token(self) -> None:
        store, _ = make_store()
        store.create(make_state())
        lease = store.acquire_lease(
            "wf-1", owner_id="worker-a", tenant_scope_fingerprint=None, ttl_seconds=60.0
        )
        next_state = queued(make_state())
        store.update(
            "wf-1",
            WorkflowStatus.CREATED,
            next_state,
            expected_version=1,
            owner_id="worker-a",
            fencing_token=lease.fencing_token,
        )
        with pytest.raises(SharedStoreError) as excinfo:
            store.update(
                "wf-1",
                WorkflowStatus.QUEUED,
                queued(make_state()),
                expected_version=2,
                owner_id="worker-intruder",
                fencing_token=lease.fencing_token,
            )
        assert excinfo.value.code is SharedStoreErrorCode.FENCING_REJECTED
        stored = store.get("wf-1")
        assert stored is not None and stored.status is WorkflowStatus.QUEUED


class TestIdempotencyAtomicity:
    def test_concurrent_reservation_has_one_binding(self) -> None:
        store, _ = make_store()
        store.reserve_idempotency(
            "key-1",
            request_id="req-1",
            workflow_id="wf-1",
            tenant_scope_fingerprint=SCOPE_A,
        )
        with pytest.raises(IdempotencyConflictError):
            store.reserve_idempotency(
                "key-1",
                request_id="req-2",
                workflow_id="wf-2",
                tenant_scope_fingerprint=SCOPE_A,
            )
        bound = store.get_idempotency("key-1", tenant_scope_fingerprint=SCOPE_A)
        assert bound is not None and bound.request_id == "req-1"

    def test_re_reservation_with_same_identity_is_stable(self) -> None:
        store, _ = make_store()
        first = store.reserve_idempotency(
            "key-1",
            request_id="req-1",
            workflow_id="wf-1",
            tenant_scope_fingerprint=SCOPE_A,
        )
        second = store.reserve_idempotency(
            "key-1",
            request_id="req-1",
            workflow_id="wf-1",
            tenant_scope_fingerprint=SCOPE_A,
        )
        assert first == second

    def test_completion_is_atomic_and_never_overwritten(self) -> None:
        store, _ = make_store()
        store.reserve_idempotency(
            "key-1",
            request_id="req-1",
            workflow_id="wf-1",
            tenant_scope_fingerprint=SCOPE_A,
        )
        completed = store.complete_idempotency(
            "key-1",
            workflow_id="wf-1",
            terminal_outcome_fingerprint=FINGERPRINT,
            tenant_scope_fingerprint=SCOPE_A,
        )
        assert completed.status is IdempotencyStatus.COMPLETED
        assert completed.terminal_outcome_fingerprint == FINGERPRINT
        with pytest.raises(IdempotencyConflictError) as excinfo:
            store.complete_idempotency(
                "key-1",
                workflow_id="wf-1",
                terminal_outcome_fingerprint="sha256:" + "e" * 64,
                tenant_scope_fingerprint=SCOPE_A,
            )
        assert "already completed" in str(excinfo.value)

    def test_fenced_completion_requires_current_owner(self) -> None:
        store, _ = make_store()
        store.reserve_idempotency(
            "key-1",
            request_id="req-1",
            workflow_id="wf-1",
            tenant_scope_fingerprint=SCOPE_A,
        )
        store.acquire_lease(
            "wf-1", owner_id="worker-a", tenant_scope_fingerprint=SCOPE_A, ttl_seconds=60.0
        )
        with pytest.raises(SharedStoreError) as excinfo:
            store.complete_idempotency(
                "key-1",
                workflow_id="wf-1",
                terminal_outcome_fingerprint=FINGERPRINT,
                tenant_scope_fingerprint=SCOPE_A,
                owner_id="worker-intruder",
                fencing_token=1,
            )
        assert excinfo.value.code is SharedStoreErrorCode.FENCING_REJECTED
        record = store.get_idempotency("key-1", tenant_scope_fingerprint=SCOPE_A)
        assert record is not None and record.status is IdempotencyStatus.RESERVED

    def test_expired_key_is_reusable(self) -> None:
        store, pool = make_store()
        store.reserve_idempotency(
            "key-1",
            request_id="req-1",
            workflow_id="wf-1",
            tenant_scope_fingerprint=SCOPE_A,
            expires_at=pool.clock.now() - timedelta(seconds=10),
        )
        rebound = store.reserve_idempotency(
            "key-1",
            request_id="req-2",
            workflow_id="wf-2",
            tenant_scope_fingerprint=SCOPE_A,
        )
        assert rebound.request_id == "req-2"

    def test_completed_request_is_replay_safe(self) -> None:
        store, _ = make_store()
        store.reserve_idempotency(
            "key-1",
            request_id="req-1",
            workflow_id="wf-1",
            tenant_scope_fingerprint=SCOPE_A,
        )
        store.complete_idempotency(
            "key-1",
            workflow_id="wf-1",
            terminal_outcome_fingerprint=FINGERPRINT,
            tenant_scope_fingerprint=SCOPE_A,
        )
        replay = store.get_idempotency("key-1", tenant_scope_fingerprint=SCOPE_A)
        assert replay is not None
        assert replay.status is IdempotencyStatus.COMPLETED
        assert replay.terminal_outcome_fingerprint == FINGERPRINT


class TestLeaseLifecycle:
    def test_acquire_claims_with_token_and_expiry(self) -> None:
        store, pool = make_store()
        lease = store.acquire_lease(
            "wf-1", owner_id="worker-a", tenant_scope_fingerprint=SCOPE_A, ttl_seconds=60.0
        )
        assert lease.owner_id == "worker-a"
        assert lease.fencing_token == 1
        assert lease.tenant_scope_fingerprint == SCOPE_A
        assert lease.expires_at == pool.clock.now() + timedelta(seconds=60.0)
        assert lease.valid(now=pool.clock.now()) is True

    def test_active_lease_excludes_other_workers_without_changing_ownership(self) -> None:
        store, _ = make_store()
        store.acquire_lease(
            "wf-1", owner_id="worker-a", tenant_scope_fingerprint=SCOPE_A, ttl_seconds=60.0
        )
        with pytest.raises(SharedStoreError) as excinfo:
            store.acquire_lease(
                "wf-1",
                owner_id="worker-b",
                tenant_scope_fingerprint=SCOPE_A,
                ttl_seconds=60.0,
            )
        assert excinfo.value.code is SharedStoreErrorCode.LEASE_BUSY
        assert excinfo.value.retryable is True
        lease = store.inspect_lease("wf-1", tenant_scope_fingerprint=SCOPE_A)
        assert lease is not None and lease.owner_id == "worker-a"

    def test_renewal_extends_only_for_the_current_owner_and_token(self) -> None:
        store, pool = make_store()
        lease = store.acquire_lease(
            "wf-1", owner_id="worker-a", tenant_scope_fingerprint=SCOPE_A, ttl_seconds=60.0
        )
        pool.clock.advance(30.0)
        renewed = store.renew_lease(
            "wf-1",
            owner_id="worker-a",
            fencing_token=lease.fencing_token,
            tenant_scope_fingerprint=SCOPE_A,
        )
        assert renewed.fencing_token == lease.fencing_token
        assert renewed.expires_at > lease.expires_at
        with pytest.raises(SharedStoreError) as wrong_owner:
            store.renew_lease(
                "wf-1",
                owner_id="worker-b",
                fencing_token=lease.fencing_token,
                tenant_scope_fingerprint=SCOPE_A,
            )
        assert wrong_owner.value.code is SharedStoreErrorCode.FENCING_REJECTED
        with pytest.raises(SharedStoreError) as wrong_token:
            store.renew_lease(
                "wf-1",
                owner_id="worker-a",
                fencing_token=lease.fencing_token + 1,
                tenant_scope_fingerprint=SCOPE_A,
            )
        assert wrong_token.value.code is SharedStoreErrorCode.FENCING_REJECTED

    def test_release_is_owner_checked_and_idempotent(self) -> None:
        store, _ = make_store()
        lease = store.acquire_lease(
            "wf-1", owner_id="worker-a", tenant_scope_fingerprint=SCOPE_A, ttl_seconds=60.0
        )
        assert (
            store.release_lease(
                "wf-1",
                owner_id="worker-b",
                fencing_token=lease.fencing_token,
                tenant_scope_fingerprint=SCOPE_A,
            )
            is False
        )
        assert store.inspect_lease("wf-1", tenant_scope_fingerprint=SCOPE_A) is not None
        assert (
            store.release_lease(
                "wf-1",
                owner_id="worker-a",
                fencing_token=lease.fencing_token,
                tenant_scope_fingerprint=SCOPE_A,
            )
            is True
        )
        assert store.inspect_lease("wf-1", tenant_scope_fingerprint=SCOPE_A) is None

    def test_expired_lease_cannot_be_renewed(self) -> None:
        store, pool = make_store()
        lease = store.acquire_lease(
            "wf-1", owner_id="worker-a", tenant_scope_fingerprint=SCOPE_A, ttl_seconds=60.0
        )
        pool.clock.advance(61.0)
        assert lease.valid(now=pool.clock.now()) is False
        with pytest.raises(SharedStoreError) as excinfo:
            store.renew_lease(
                "wf-1",
                owner_id="worker-a",
                fencing_token=lease.fencing_token,
                tenant_scope_fingerprint=SCOPE_A,
            )
        assert excinfo.value.code is SharedStoreErrorCode.FENCING_REJECTED


class TestTakeoverAndFencing:
    def test_takeover_is_refused_before_expiry(self) -> None:
        store, pool = make_store()
        store.acquire_lease(
            "wf-1", owner_id="worker-a", tenant_scope_fingerprint=SCOPE_A, ttl_seconds=60.0
        )
        pool.clock.advance(59.0)  # still within TTL and tolerance
        with pytest.raises(SharedStoreError) as excinfo:
            store.acquire_lease(
                "wf-1",
                owner_id="worker-b",
                tenant_scope_fingerprint=SCOPE_A,
                ttl_seconds=60.0,
            )
        assert excinfo.value.code is SharedStoreErrorCode.LEASE_BUSY

    def test_expired_lease_is_recovered_with_greater_fencing_token(self) -> None:
        store, pool = make_store()
        old = store.acquire_lease(
            "wf-1", owner_id="worker-a", tenant_scope_fingerprint=SCOPE_A, ttl_seconds=60.0
        )
        pool.clock.advance(62.0)  # past TTL + clock tolerance
        new = store.acquire_lease(
            "wf-1", owner_id="worker-b", tenant_scope_fingerprint=SCOPE_A, ttl_seconds=60.0
        )
        assert new.owner_id == "worker-b"
        assert new.fencing_token > old.fencing_token

    def test_lost_owner_cannot_commit_state_after_takeover(self) -> None:
        store, pool = make_store()
        store.create(make_state(scope=SCOPE_A))
        old = store.acquire_lease(
            "wf-1", owner_id="worker-a", tenant_scope_fingerprint=SCOPE_A, ttl_seconds=60.0
        )
        pool.clock.advance(62.0)
        store.acquire_lease(
            "wf-1", owner_id="worker-b", tenant_scope_fingerprint=SCOPE_A, ttl_seconds=60.0
        )
        with pytest.raises(SharedStoreError) as excinfo:
            store.update(
                "wf-1",
                WorkflowStatus.CREATED,
                queued(make_state(scope=SCOPE_A)),
                expected_version=1,
                tenant_scope_fingerprint=SCOPE_A,
                owner_id="worker-a",
                fencing_token=old.fencing_token,
            )
        assert excinfo.value.code is SharedStoreErrorCode.FENCING_REJECTED
        lease = store.inspect_lease("wf-1", tenant_scope_fingerprint=SCOPE_A)
        assert lease is not None
        store.update(
            "wf-1",
            WorkflowStatus.CREATED,
            queued(make_state(scope=SCOPE_A)),
            expected_version=1,
            tenant_scope_fingerprint=SCOPE_A,
            owner_id="worker-b",
            fencing_token=lease.fencing_token,
        )
        stored = store.get("wf-1", tenant_scope_fingerprint=SCOPE_A)
        assert stored is not None and stored.status is WorkflowStatus.QUEUED

    def test_lost_owner_cannot_complete_idempotency_after_takeover(self) -> None:
        store, pool = make_store()
        store.reserve_idempotency(
            "key-1",
            request_id="req-1",
            workflow_id="wf-1",
            tenant_scope_fingerprint=SCOPE_A,
        )
        old = store.acquire_lease(
            "wf-1", owner_id="worker-a", tenant_scope_fingerprint=SCOPE_A, ttl_seconds=60.0
        )
        pool.clock.advance(62.0)
        new = store.acquire_lease(
            "wf-1", owner_id="worker-b", tenant_scope_fingerprint=SCOPE_A, ttl_seconds=60.0
        )
        with pytest.raises(SharedStoreError) as excinfo:
            store.complete_idempotency(
                "key-1",
                workflow_id="wf-1",
                terminal_outcome_fingerprint=FINGERPRINT,
                tenant_scope_fingerprint=SCOPE_A,
                owner_id="worker-a",
                fencing_token=old.fencing_token,
            )
        assert excinfo.value.code is SharedStoreErrorCode.FENCING_REJECTED
        completed = store.complete_idempotency(
            "key-1",
            workflow_id="wf-1",
            terminal_outcome_fingerprint=FINGERPRINT,
            tenant_scope_fingerprint=SCOPE_A,
            owner_id="worker-b",
            fencing_token=new.fencing_token,
        )
        assert completed.status is IdempotencyStatus.COMPLETED

    def test_lease_scope_namespace_is_never_exposed_through_records(self) -> None:
        store, _ = make_store()
        store.acquire_lease(
            "wf-1", owner_id="worker-a", tenant_scope_fingerprint=SCOPE_A, ttl_seconds=60.0
        )
        lease = store.inspect_lease("wf-1", tenant_scope_fingerprint=SCOPE_A)
        assert lease is not None
        from nl2data_core.workflow.durable import tenant_scope_namespace
        assert tenant_scope_namespace(SCOPE_A) not in (lease.owner_id, lease.workflow_id)
