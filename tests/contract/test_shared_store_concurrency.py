"""Concurrency tests for the shared workflow state backend.

Proves the backend keeps exactly one active owner, rejects stale commits,
suppresses duplicate reservations, and never completes a terminal outcome
twice - even when two worker threads race through separate store instances
over the same pool.  The fake pool serializes conflicting statements on row
locks exactly like PostgreSQL row locking, so outcomes are deterministic:
exactly one racer wins and the loser receives a structured error.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from nl2data_core.workflow.durable import IdempotencyConflictError, IdempotencyStatus
from nl2data_core.workflow.fake_postgres import FakePostgresPool
from nl2data_core.workflow.models import (
    WorkflowState,
    WorkflowStateError,
    WorkflowStatus,
)
from nl2data_core.workflow.postgres_store import PostgreSQLStateStore
from nl2data_core.workflow.shared_errors import SharedStoreError, SharedStoreErrorCode
from nl2data_core.workflow.transitions import transition

SCOPE_A = "sha256:" + "a" * 64
FINGERPRINT = "sha256:" + "f" * 64
FINGERPRINT_B = "sha256:" + "e" * 64


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


def make_stores(
    pool: FakePostgresPool | None = None,
) -> tuple[PostgreSQLStateStore, PostgreSQLStateStore, FakePostgresPool]:
    pool = pool or FakePostgresPool()
    store_a = PostgreSQLStateStore(pool=pool, now=pool.clock.now)
    store_b = PostgreSQLStateStore(pool=pool, now=pool.clock.now)
    return store_a, store_b, pool


def race(
    fn_a: Callable[[], object], fn_b: Callable[[], object]
) -> dict[str, tuple[str, object]]:
    """Run two functions concurrently from a barrier; capture outcomes."""

    barrier = threading.Barrier(2)
    results: dict[str, tuple[str, object]] = {}

    def runner(name: str, fn: Callable[[], object]) -> None:
        try:
            barrier.wait(timeout=10)
            results[name] = ("ok", fn())
        except BaseException as error:  # noqa: BLE001 - surfaced to the test
            results[name] = ("error", error)

    threads = [
        threading.Thread(target=runner, args=("a", fn_a)),
        threading.Thread(target=runner, args=("b", fn_b)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results


class TestOneActiveOwner:
    def test_concurrent_lease_acquisition_has_one_winner(self) -> None:
        store_a, store_b, pool = make_stores()
        results = race(
            lambda: store_a.acquire_lease(
                "wf-1",
                owner_id="worker-a",
                tenant_scope_fingerprint=SCOPE_A,
                ttl_seconds=60.0,
            ),
            lambda: store_b.acquire_lease(
                "wf-1",
                owner_id="worker-b",
                tenant_scope_fingerprint=SCOPE_A,
                ttl_seconds=60.0,
            ),
        )
        winners = [name for name, (outcome, _) in results.items() if outcome == "ok"]
        assert winners == ["a"] or winners == ["b"]
        loser = "b" if winners == ["a"] else "a"
        outcome, error = results[loser]
        assert outcome == "error"
        assert isinstance(error, SharedStoreError)
        assert error.code is SharedStoreErrorCode.LEASE_BUSY
        assert error.retryable is True
        # The winner holds the only lease with the first fencing token.
        lease = store_a.inspect_lease("wf-1", tenant_scope_fingerprint=SCOPE_A)
        assert lease is not None
        assert lease.fencing_token == 1
        assert lease.owner_id == ("worker-a" if winners == ["a"] else "worker-b")

    def test_concurrent_renewal_and_takeover_keep_one_owner(self) -> None:
        store_a, store_b, pool = make_stores()
        old = store_a.acquire_lease(
            "wf-1",
            owner_id="worker-a",
            tenant_scope_fingerprint=SCOPE_A,
            ttl_seconds=60.0,
        )
        pool.clock.advance(62.0)
        store_b.acquire_lease(
            "wf-1",
            owner_id="worker-b",
            tenant_scope_fingerprint=SCOPE_A,
            ttl_seconds=60.0,
        )
        # The stale owner's renewal races the new owner's renewal; only the
        # current owner and token can win.
        results = race(
            lambda: store_a.renew_lease(
                "wf-1",
                owner_id="worker-a",
                fencing_token=old.fencing_token,
                tenant_scope_fingerprint=SCOPE_A,
            ),
            lambda: store_b.renew_lease(
                "wf-1",
                owner_id="worker-b",
                fencing_token=2,
                tenant_scope_fingerprint=SCOPE_A,
            ),
        )
        assert results["b"][0] == "ok"
        assert results["a"][0] == "error"
        assert isinstance(results["a"][1], SharedStoreError)
        assert results["a"][1].code is SharedStoreErrorCode.FENCING_REJECTED
        lease = store_a.inspect_lease("wf-1", tenant_scope_fingerprint=SCOPE_A)
        assert lease is not None
        assert lease.owner_id == "worker-b"
        assert lease.fencing_token == 2


class TestStaleCommitRejection:
    def test_concurrent_fenced_commits_after_takeover(self) -> None:
        store_a, store_b, pool = make_stores()
        store_a.create(make_state(scope=SCOPE_A))
        old = store_a.acquire_lease(
            "wf-1",
            owner_id="worker-a",
            tenant_scope_fingerprint=SCOPE_A,
            ttl_seconds=60.0,
        )
        pool.clock.advance(62.0)
        new = store_b.acquire_lease(
            "wf-1",
            owner_id="worker-b",
            tenant_scope_fingerprint=SCOPE_A,
            ttl_seconds=60.0,
        )
        # Both workers attempt a fenced commit concurrently; the stale owner
        # is rejected and the current owner's commit lands.
        results = race(
            lambda: store_a.update(
                "wf-1",
                WorkflowStatus.CREATED,
                queued(make_state(scope=SCOPE_A, request_id="req-stale")),
                expected_version=1,
                tenant_scope_fingerprint=SCOPE_A,
                owner_id="worker-a",
                fencing_token=old.fencing_token,
            ),
            lambda: store_b.update(
                "wf-1",
                WorkflowStatus.CREATED,
                queued(make_state(scope=SCOPE_A, request_id="req-current")),
                expected_version=1,
                tenant_scope_fingerprint=SCOPE_A,
                owner_id="worker-b",
                fencing_token=new.fencing_token,
            ),
        )
        assert results["b"][0] == "ok"
        assert results["a"][0] == "error"
        # The stale owner is rejected either by fencing or by the CAS
        # conflict the current owner's commit left behind - never committed.
        outcome, error = results["a"]
        assert outcome == "error"
        assert isinstance(error, (SharedStoreError, WorkflowStateError))
        if isinstance(error, SharedStoreError):
            assert error.code is SharedStoreErrorCode.FENCING_REJECTED
        stored = store_a.get("wf-1", tenant_scope_fingerprint=SCOPE_A)
        assert stored is not None
        assert stored.request_id == "req-current"
        assert stored.status is WorkflowStatus.QUEUED

    def test_concurrent_cas_updates_have_one_winner(self) -> None:
        store, _, _ = make_stores()
        store.create(make_state())
        results = race(
            lambda: store.update(
                "wf-1",
                WorkflowStatus.CREATED,
                queued(make_state(request_id="req-a")),
                expected_version=1,
            ),
            lambda: store.update(
                "wf-1",
                WorkflowStatus.CREATED,
                queued(make_state(request_id="req-b")),
                expected_version=1,
            ),
        )
        winners = [name for name, (outcome, _) in results.items() if outcome == "ok"]
        assert winners == ["a"] or winners == ["b"]
        loser = "b" if winners == ["a"] else "a"
        outcome, error = results[loser]
        assert outcome == "error"
        assert isinstance(error, WorkflowStateError)
        # The loser observes the winner's commit as a status or version
        # conflict, depending on which statement landed first.
        assert "changed concurrently" in str(error)
        assert store.get_revision("wf-1") == 2
        stored = store.get("wf-1")
        assert stored is not None
        assert stored.request_id == ("req-a" if winners == ["a"] else "req-b")


class TestDuplicateSuppression:
    def test_concurrent_reservation_has_one_binding(self) -> None:
        store_a, store_b, _ = make_stores()
        results = race(
            lambda: store_a.reserve_idempotency(
                "key-1",
                request_id="req-1",
                workflow_id="wf-1",
                tenant_scope_fingerprint=SCOPE_A,
            ),
            lambda: store_b.reserve_idempotency(
                "key-1",
                request_id="req-2",
                workflow_id="wf-2",
                tenant_scope_fingerprint=SCOPE_A,
            ),
        )
        winners = [name for name, (outcome, _) in results.items() if outcome == "ok"]
        assert winners == ["a"] or winners == ["b"]
        loser = "b" if winners == ["a"] else "a"
        outcome, error = results[loser]
        assert outcome == "error"
        assert isinstance(error, IdempotencyConflictError)
        bound = store_a.get_idempotency("key-1", tenant_scope_fingerprint=SCOPE_A)
        assert bound is not None
        assert bound.request_id == ("req-1" if winners == ["a"] else "req-2")

    def test_concurrent_create_has_one_winner(self) -> None:
        store_a, store_b, pool = make_stores()
        results = race(
            lambda: store_a.create(make_state(workflow_id="wf-1")),
            lambda: store_b.create(make_state(workflow_id="wf-1")),
        )
        winners = [name for name, (outcome, _) in results.items() if outcome == "ok"]
        assert winners == ["a"] or winners == ["b"]
        loser = "b" if winners == ["a"] else "a"
        outcome, error = results[loser]
        assert outcome == "error"
        assert isinstance(error, WorkflowStateError)
        assert "already exists" in str(error)
        assert len(pool.states) == 1


class TestNoDoubleTerminalCompletion:
    def test_concurrent_completion_happens_once(self) -> None:
        store_a, store_b, _ = make_stores()
        store_a.reserve_idempotency(
            "key-1",
            request_id="req-1",
            workflow_id="wf-1",
            tenant_scope_fingerprint=SCOPE_A,
        )
        results = race(
            lambda: store_a.complete_idempotency(
                "key-1",
                workflow_id="wf-1",
                terminal_outcome_fingerprint=FINGERPRINT,
                tenant_scope_fingerprint=SCOPE_A,
            ),
            lambda: store_b.complete_idempotency(
                "key-1",
                workflow_id="wf-1",
                terminal_outcome_fingerprint=FINGERPRINT_B,
                tenant_scope_fingerprint=SCOPE_A,
            ),
        )
        winners = [name for name, (outcome, _) in results.items() if outcome == "ok"]
        assert winners == ["a"] or winners == ["b"]
        loser = "b" if winners == ["a"] else "a"
        outcome, error = results[loser]
        assert outcome == "error"
        assert isinstance(error, IdempotencyConflictError)
        assert "already completed" in str(error)
        record = store_a.get_idempotency("key-1", tenant_scope_fingerprint=SCOPE_A)
        assert record is not None
        assert record.status is IdempotencyStatus.COMPLETED
        assert record.terminal_outcome_fingerprint == (
            FINGERPRINT if winners == ["a"] else FINGERPRINT_B
        )


def queued(state: WorkflowState) -> WorkflowState:
    return transition(state, WorkflowStatus.QUEUED, event_id="ev-queued")
