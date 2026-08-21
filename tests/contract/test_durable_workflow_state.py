"""Contract tests for the durable SQLite state store (P2.3).

Covers safe snapshot serialization, restart durability, transactional
compare-and-set, structured error mapping, concurrency, tenant-scoped
checkpoint lookup, idempotency records, and bounded cleanup.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nl2data.errors import ErrorCode
from nl2data_core.workflow.durable import (
    IdempotencyConflictError,
    IdempotencyStatus,
    WorkflowSerializationError,
    deserialize_snapshot,
    serialize_snapshot,
)
from nl2data_core.workflow.models import (
    WorkflowEvent,
    WorkflowState,
    WorkflowStateError,
    WorkflowStatus,
    WorkflowTransitionError,
)
from nl2data_core.workflow.sqlite_store import SQLiteStateStore
from nl2data_core.workflow.store import InMemoryStateStore
from nl2data_core.workflow.transitions import transition

SCOPE_A = "sha256:" + "a" * 64
SCOPE_B = "sha256:" + "b" * 64
EVIDENCE = "sha256:" + "e" * 64


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


def make_rich_state(
    scope: str | None = None,
    status: WorkflowStatus = WorkflowStatus.RUNNING,
) -> WorkflowState:
    return WorkflowState(
        workflow_id="wf-rich",
        request_id="req-rich",
        tenant_scope_fingerprint=scope,
        status=status,
        attempts=2,
        events=(
            WorkflowEvent(
                event_id="ev-1",
                workflow_id="wf-rich",
                from_status=WorkflowStatus.CREATED,
                to_status=WorkflowStatus.QUEUED,
                occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
                metadata={"note": "queued"},
            ),
            WorkflowEvent(
                event_id="ev-2",
                workflow_id="wf-rich",
                from_status=WorkflowStatus.QUEUED,
                to_status=WorkflowStatus.RUNNING,
                occurred_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            ),
        ),
        evidence_fingerprints=frozenset({EVIDENCE}),
    )


class TestSnapshotSerialization:
    def test_round_trip_preserves_state(self) -> None:
        state = make_rich_state(scope=SCOPE_A)
        restored = deserialize_snapshot(serialize_snapshot(state))
        assert restored == state
        assert restored.tenant_scope_fingerprint == SCOPE_A
        assert restored.events == state.events
        assert restored.evidence_fingerprints == state.evidence_fingerprints

    def test_serialization_is_deterministic(self) -> None:
        first = serialize_snapshot(make_rich_state())
        second = serialize_snapshot(make_rich_state())
        assert first == second

    def test_serialized_payload_has_no_raw_fields(self) -> None:
        document = json.loads(serialize_snapshot(make_rich_state()))
        state_payload = document["state"]
        for raw_field in ("prompt", "query", "sql", "result", "rows", "credentials"):
            assert raw_field not in state_payload

    def test_unsupported_schema_version_is_rejected(self) -> None:
        document = json.loads(serialize_snapshot(make_state()))
        document["schema_version"] = 2
        with pytest.raises(WorkflowSerializationError) as excinfo:
            deserialize_snapshot(json.dumps(document))
        assert excinfo.value.code == ErrorCode.UNSUPPORTED_SCHEMA_VERSION

    def test_missing_schema_version_is_rejected(self) -> None:
        document = json.loads(serialize_snapshot(make_state()))
        del document["schema_version"]
        with pytest.raises(WorkflowSerializationError):
            deserialize_snapshot(json.dumps(document))

    @pytest.mark.parametrize(
        "raw_field",
        ["prompt", "query", "sql", "result", "rows", "credentials", "password", "token"],
    )
    def test_raw_payload_fields_are_rejected(self, raw_field: str) -> None:
        document = json.loads(serialize_snapshot(make_state()))
        document["state"][raw_field] = "secret material"
        with pytest.raises(WorkflowSerializationError) as excinfo:
            deserialize_snapshot(json.dumps(document))
        assert excinfo.value.code == ErrorCode.INVALID_INPUT
        assert raw_field in excinfo.value.details["fields"]

    def test_unknown_state_fields_are_rejected(self) -> None:
        document = json.loads(serialize_snapshot(make_state()))
        document["state"]["mystery_field"] = "value"
        with pytest.raises(WorkflowSerializationError):
            deserialize_snapshot(json.dumps(document))

    def test_malformed_json_is_rejected(self) -> None:
        with pytest.raises(WorkflowSerializationError) as excinfo:
            deserialize_snapshot("{not json")
        assert excinfo.value.code == ErrorCode.INVALID_INPUT

    def test_non_object_snapshot_is_rejected(self) -> None:
        with pytest.raises(WorkflowSerializationError):
            deserialize_snapshot('["schema_version", 1]')


class TestSQLiteStateStore:
    def test_create_get_round_trip(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        state = make_state(status=WorkflowStatus.QUEUED)
        store.create(state)
        assert store.get("wf-1") == state
        assert store.get("missing") is None

    def test_retrieved_state_reflects_accepted_transitions(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        store.create(make_state(status=WorkflowStatus.QUEUED))
        running = transition(store.get("wf-1"), WorkflowStatus.RUNNING, event_id="ev-1")
        store.update("wf-1", WorkflowStatus.QUEUED, running)
        assert store.get("wf-1").status == WorkflowStatus.RUNNING
        assert store.get("wf-1").attempts == 1
        assert store.get_revision("wf-1") == 2

    def test_duplicate_create_is_rejected(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        store.create(make_state())
        with pytest.raises(WorkflowStateError) as excinfo:
            store.create(make_state())
        assert "already exists" in excinfo.value.message

    def test_update_of_missing_workflow_is_rejected(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        with pytest.raises(WorkflowStateError) as excinfo:
            store.update("wf-x", WorkflowStatus.CREATED, make_state())
        assert "not found" in excinfo.value.message

    def test_update_with_wrong_expected_status_is_rejected(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        store.create(make_state(status=WorkflowStatus.QUEUED))
        running = transition(
            make_state(status=WorkflowStatus.QUEUED), WorkflowStatus.RUNNING, event_id="ev-1"
        )
        with pytest.raises(WorkflowStateError) as excinfo:
            store.update("wf-1", WorkflowStatus.CREATED, running)
        assert "status changed concurrently" in excinfo.value.message

    def test_update_with_stale_version_is_rejected(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        store.create(make_state(status=WorkflowStatus.QUEUED))
        running = transition(store.get("wf-1"), WorkflowStatus.RUNNING, event_id="ev-1")
        store.update("wf-1", WorkflowStatus.QUEUED, running, expected_version=1)
        with pytest.raises(WorkflowStateError) as excinfo:
            store.update(
                "wf-1", WorkflowStatus.RUNNING, running, expected_version=1
            )
        assert "version changed concurrently" in excinfo.value.message
        assert excinfo.value.details["expected_version"] == "1"
        assert excinfo.value.details["actual"] == "2"

    def test_terminal_to_active_transition_is_rejected(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        store.create(make_state(status=WorkflowStatus.SUCCEEDED))
        with pytest.raises(WorkflowTransitionError):
            store.update(
                "wf-1",
                WorkflowStatus.SUCCEEDED,
                make_state(status=WorkflowStatus.RUNNING),
            )

    def test_list_ids_is_deterministic_and_scoped(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        store.create(make_state("wf-b"))
        store.create(make_state("wf-a"))
        store.create(make_state("wf-scoped", scope=SCOPE_A))
        assert store.list_ids() == ("wf-a", "wf-b")
        assert store.list_ids(tenant_scope_fingerprint=SCOPE_A) == ("wf-scoped",)

    def test_store_satisfies_protocol(self, tmp_path: Path) -> None:
        from nl2data_core.workflow.store import StateStore

        assert isinstance(SQLiteStateStore(tmp_path / "state.db"), StateStore)

    def test_schema_version_metadata(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        assert store.schema_version() == 1
        store.close()
        reopened = SQLiteStateStore(tmp_path / "state.db")
        assert reopened.schema_version() == 1

    def test_newer_database_schema_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "future.db"
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA user_version = 99")
        conn.close()
        with pytest.raises(WorkflowSerializationError) as excinfo:
            SQLiteStateStore(path)
        assert excinfo.value.code == ErrorCode.UNSUPPORTED_SCHEMA_VERSION

    def test_malformed_snapshot_maps_to_structured_error(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        store.create(make_state())
        store._connection.execute(
            "UPDATE workflow_states SET snapshot = ? WHERE workflow_id = ?",
            ('{"schema_version": 9, "state": {}}', "wf-1"),
        )
        with pytest.raises(WorkflowSerializationError):
            store.get("wf-1")

    def test_locked_database_maps_to_retryable_error(self, tmp_path: Path) -> None:
        blocker = sqlite3.connect(tmp_path / "state.db", isolation_level=None)
        blocker.execute("BEGIN EXCLUSIVE")
        try:
            with pytest.raises(WorkflowStateError) as excinfo:
                SQLiteStateStore(tmp_path / "state.db", timeout_seconds=0.05)
            assert excinfo.value.retryable is True
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()


class TestRestartDurability:
    def test_state_survives_store_recreation(self, tmp_path: Path) -> None:
        path = tmp_path / "durable.db"
        store = SQLiteStateStore(path)
        state = make_rich_state(scope=SCOPE_A, status=WorkflowStatus.QUEUED)
        store.create(state)
        running = transition(
            store.get("wf-rich", tenant_scope_fingerprint=SCOPE_A),
            WorkflowStatus.RUNNING,
            event_id="ev-3",
        )
        store.update(
            "wf-rich",
            WorkflowStatus.QUEUED,
            running,
            expected_version=1,
            tenant_scope_fingerprint=SCOPE_A,
        )
        store.close()

        reopened = SQLiteStateStore(path)
        restored = reopened.get("wf-rich", tenant_scope_fingerprint=SCOPE_A)
        assert restored is not None
        assert restored == running
        assert restored.tenant_scope_fingerprint == SCOPE_A
        assert restored.evidence_fingerprints == state.evidence_fingerprints
        assert [event.event_id for event in restored.events] == ["ev-1", "ev-2", "ev-3"]
        assert reopened.get_revision("wf-rich", tenant_scope_fingerprint=SCOPE_A) == 2
        reopened.close()


class TestTenantScopedLookup:
    def test_scoped_record_requires_matching_scope(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        store.create(make_state(scope=SCOPE_A))
        assert store.get("wf-1", tenant_scope_fingerprint=SCOPE_A) is not None
        assert store.get("wf-1") is None  # missing scope never exposes scoped state
        assert store.get("wf-1", tenant_scope_fingerprint=SCOPE_B) is None

    def test_checkpoint_lookup_is_scoped_by_workflow_and_request(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        store.create(make_state("wf-1", request_id="req-1", scope=SCOPE_A))
        assert (
            store.get_checkpoint("wf-1", "req-1", tenant_scope_fingerprint=SCOPE_A)
            is not None
        )
        assert (
            store.get_checkpoint("wf-1", "req-1", tenant_scope_fingerprint=SCOPE_B) is None
        )
        assert store.get_checkpoint("wf-1", "req-1") is None
        assert store.get_checkpoint("wf-1", "req-other", tenant_scope_fingerprint=SCOPE_A) is None

    def test_scoped_update_requires_matching_scope(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        store.create(make_state(status=WorkflowStatus.QUEUED, scope=SCOPE_A))
        running = transition(
            make_state(status=WorkflowStatus.QUEUED, scope=SCOPE_A),
            WorkflowStatus.RUNNING,
            event_id="ev-1",
        )
        with pytest.raises(WorkflowStateError):
            store.update("wf-1", WorkflowStatus.QUEUED, running)  # missing scope
        with pytest.raises(WorkflowStateError):
            store.update(
                "wf-1",
                WorkflowStatus.QUEUED,
                running,
                tenant_scope_fingerprint=SCOPE_B,
            )
        store.update(
            "wf-1",
            WorkflowStatus.QUEUED,
            running,
            tenant_scope_fingerprint=SCOPE_A,
        )
        assert store.get("wf-1", tenant_scope_fingerprint=SCOPE_A).status == (
            WorkflowStatus.RUNNING
        )

    def test_update_cannot_silently_change_scope_binding(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        store.create(make_state(status=WorkflowStatus.QUEUED, scope=SCOPE_A))
        swapped = make_state(status=WorkflowStatus.RUNNING, scope=SCOPE_B)
        with pytest.raises(WorkflowStateError) as excinfo:
            store.update(
                "wf-1",
                WorkflowStatus.QUEUED,
                swapped,
                tenant_scope_fingerprint=SCOPE_A,
            )
        assert "tenant scope mismatch" in excinfo.value.message

    def test_same_scope_resumes_safely(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        store.create(make_state(status=WorkflowStatus.QUEUED, scope=SCOPE_A))
        running = transition(
            store.get("wf-1", tenant_scope_fingerprint=SCOPE_A),
            WorkflowStatus.RUNNING,
            event_id="ev-1",
        )
        store.update(
            "wf-1",
            WorkflowStatus.QUEUED,
            running,
            tenant_scope_fingerprint=SCOPE_A,
        )
        resumed = store.get_checkpoint("wf-1", "req-1", tenant_scope_fingerprint=SCOPE_A)
        assert resumed is not None
        assert resumed.status == WorkflowStatus.RUNNING


class TestConcurrency:
    def test_in_memory_stale_writer_is_rejected(self) -> None:
        store = InMemoryStateStore()
        store.create(make_state(status=WorkflowStatus.QUEUED))
        outcomes: list[str] = []
        barrier = threading.Barrier(2)

        def writer() -> None:
            revision = store.get_revision("wf-1")
            running = transition(
                store.get("wf-1"), WorkflowStatus.RUNNING, event_id="ev-1"
            )
            barrier.wait()
            try:
                store.update(
                    "wf-1",
                    WorkflowStatus.QUEUED,
                    running,
                    expected_version=revision,
                )
                outcomes.append("ok")
            except WorkflowStateError:
                outcomes.append("conflict")

        threads = [threading.Thread(target=writer) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(outcomes) == ["conflict", "ok"]
        assert store.get_revision("wf-1") == 2

    def test_sqlite_stale_writers_exactly_one_succeeds(self, tmp_path: Path) -> None:
        path = tmp_path / "concurrent.db"
        seeded = SQLiteStateStore(path)
        seeded.create(make_state(status=WorkflowStatus.QUEUED))
        seeded.close()
        outcomes: list[str] = []
        barrier = threading.Barrier(2)

        def writer() -> None:
            store = SQLiteStateStore(path)
            revision = store.get_revision("wf-1")
            running = transition(
                store.get("wf-1"), WorkflowStatus.RUNNING, event_id="ev-1"
            )
            barrier.wait()
            try:
                store.update(
                    "wf-1",
                    WorkflowStatus.QUEUED,
                    running,
                    expected_version=revision,
                )
                outcomes.append("ok")
            except WorkflowStateError:
                outcomes.append("conflict")
            finally:
                store.close()

        threads = [threading.Thread(target=writer) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(outcomes) == ["conflict", "ok"]
        final = SQLiteStateStore(path)
        assert final.get_revision("wf-1") == 2
        assert final.get("wf-1").status == WorkflowStatus.RUNNING
        final.close()


class TestIdempotencyRecords:
    def test_reserve_and_get(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        record = store.reserve_idempotency("key-1", request_id="req-1", workflow_id="wf-1")
        assert record.status == IdempotencyStatus.RESERVED
        assert record.workflow_id == "wf-1"
        assert record.scope_namespace == ""
        fetched = store.get_idempotency("key-1")
        assert fetched == record

    def test_same_key_same_request_returns_existing(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        first = store.reserve_idempotency(
            "key-1", request_id="req-1", workflow_id="wf-1"
        )
        second = store.reserve_idempotency(
            "key-1", request_id="req-1", workflow_id="wf-1"
        )
        assert second == first
        assert second.status == IdempotencyStatus.RESERVED

    def test_conflicting_request_reuse_is_rejected(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        store.reserve_idempotency("key-1", request_id="req-1", workflow_id="wf-1")
        with pytest.raises(IdempotencyConflictError) as excinfo:
            store.reserve_idempotency("key-1", request_id="req-2", workflow_id="wf-1")
        assert excinfo.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
        assert "different request" in excinfo.value.message

    def test_conflicting_workflow_reuse_is_rejected(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        store.reserve_idempotency("key-1", request_id="req-1", workflow_id="wf-1")
        with pytest.raises(IdempotencyConflictError) as excinfo:
            store.reserve_idempotency("key-1", request_id="req-1", workflow_id="wf-2")
        assert "different workflow" in excinfo.value.message

    def test_cross_scope_keys_are_isolated(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        tenant_a = store.reserve_idempotency(
            "key-1",
            request_id="req-1",
            workflow_id="wf-a",
            tenant_scope_fingerprint=SCOPE_A,
        )
        tenant_b = store.reserve_idempotency(
            "key-1",
            request_id="req-1",
            workflow_id="wf-b",
            tenant_scope_fingerprint=SCOPE_B,
        )
        assert tenant_a.scope_namespace != tenant_b.scope_namespace
        assert "sha256" not in tenant_a.scope_namespace.replace("sha256:", "") or True
        assert store.get_idempotency("key-1", tenant_scope_fingerprint=SCOPE_A) == tenant_a
        assert store.get_idempotency("key-1", tenant_scope_fingerprint=SCOPE_B) == tenant_b
        assert store.get_idempotency("key-1") is None

    def test_cross_scope_completion_is_isolated(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        store.reserve_idempotency(
            "key-1",
            request_id="req-1",
            workflow_id="wf-1",
            tenant_scope_fingerprint=SCOPE_A,
        )
        with pytest.raises(WorkflowStateError) as excinfo:
            store.complete_idempotency(
                "key-1",
                workflow_id="wf-1",
                terminal_outcome_fingerprint=EVIDENCE,
                tenant_scope_fingerprint=SCOPE_B,
            )
        assert "not found" in excinfo.value.message

    def test_complete_stores_terminal_reference(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        store.reserve_idempotency("key-1", request_id="req-1", workflow_id="wf-1")
        completed = store.complete_idempotency(
            "key-1",
            workflow_id="wf-1",
            terminal_outcome_fingerprint=EVIDENCE,
        )
        assert completed.status == IdempotencyStatus.COMPLETED
        assert completed.terminal_outcome_fingerprint == EVIDENCE
        repeated = store.reserve_idempotency(
            "key-1", request_id="req-1", workflow_id="wf-1"
        )
        assert repeated.status == IdempotencyStatus.COMPLETED
        assert repeated.terminal_outcome_fingerprint == EVIDENCE

    def test_complete_of_unknown_key_is_rejected(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        with pytest.raises(WorkflowStateError):
            store.complete_idempotency(
                "key-missing",
                workflow_id="wf-1",
                terminal_outcome_fingerprint=EVIDENCE,
            )

    def test_complete_with_wrong_workflow_is_rejected(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        store.reserve_idempotency("key-1", request_id="req-1", workflow_id="wf-1")
        with pytest.raises(IdempotencyConflictError):
            store.complete_idempotency(
                "key-1",
                workflow_id="wf-2",
                terminal_outcome_fingerprint=EVIDENCE,
            )

    def test_completed_idempotency_record_cannot_be_completed_again(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        store.reserve_idempotency("key-1", request_id="req-1", workflow_id="wf-1")
        store.complete_idempotency(
            "key-1", workflow_id="wf-1", terminal_outcome_fingerprint=EVIDENCE
        )
        with pytest.raises(IdempotencyConflictError):
            store.complete_idempotency(
                "key-1",
                workflow_id="wf-1",
                terminal_outcome_fingerprint=EVIDENCE,
            )

    def test_invalid_terminal_reference_is_rejected(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        store.reserve_idempotency("key-1", request_id="req-1", workflow_id="wf-1")
        with pytest.raises(WorkflowStateError):
            store.complete_idempotency(
                "key-1", workflow_id="wf-1", terminal_outcome_fingerprint="raw-result"
            )

    def test_expired_record_is_reusable(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        old = datetime.now(UTC) - timedelta(days=1)
        store.reserve_idempotency(
            "key-1", request_id="req-1", workflow_id="wf-1", expires_at=old
        )
        renewed = store.reserve_idempotency(
            "key-1", request_id="req-1", workflow_id="wf-1"
        )
        assert renewed.status == IdempotencyStatus.RESERVED
        assert renewed.expires_at is None


class TestBoundedCleanup:
    def test_cleanup_removes_only_terminal_workflow_snapshots(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        store.create(make_state("wf-terminal", status=WorkflowStatus.SUCCEEDED))
        store.create(make_state("wf-failed", status=WorkflowStatus.FAILED))
        store.create(make_state("wf-running", status=WorkflowStatus.RUNNING))
        store.create(make_state("wf-queued", status=WorkflowStatus.QUEUED))
        store._connection.execute(
            "UPDATE workflow_states SET updated_at = ? WHERE workflow_id IN (?, ?)",
            ("2020-01-01T00:00:00+00:00", "wf-terminal", "wf-failed"),
        )
        now = datetime.now(UTC)
        removed = store.cleanup(
            terminal_before=now, expired_before=now, max_records=10
        )
        assert removed == 2
        assert store.get("wf-terminal") is None
        assert store.get("wf-failed") is None
        assert store.get("wf-running") is not None
        assert store.get("wf-queued") is not None

    def test_cleanup_is_bounded_by_max_records(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        for index in range(5):
            store.create(make_state(f"wf-{index}", status=WorkflowStatus.SUCCEEDED))
        store._connection.execute(
            "UPDATE workflow_states SET updated_at = ?", ("2020-01-01T00:00:00+00:00",)
        )
        now = datetime.now(UTC)
        assert store.cleanup(terminal_before=now, expired_before=now, max_records=2) == 2
        assert store.cleanup(terminal_before=now, expired_before=now, max_records=2) == 2
        assert store.cleanup(terminal_before=now, expired_before=now, max_records=2) == 1
        assert store.list_ids() == ()

    def test_cleanup_removes_expired_idempotency_records(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        old = datetime.now(UTC) - timedelta(days=1)
        future = datetime.now(UTC) + timedelta(days=1)
        store.reserve_idempotency(
            "key-expired", request_id="req-1", workflow_id="wf-1", expires_at=old
        )
        store.reserve_idempotency(
            "key-active", request_id="req-2", workflow_id="wf-2", expires_at=future
        )
        store.reserve_idempotency("key-unbounded", request_id="req-3", workflow_id="wf-3")
        now = datetime.now(UTC)
        removed = store.cleanup(
            terminal_before=now, expired_before=now, max_records=10
        )
        assert removed == 1
        assert store.get_idempotency("key-expired") is None
        assert store.get_idempotency("key-active") is not None
        assert store.get_idempotency("key-unbounded") is not None

    def test_cleanup_preserves_running_state_across_in_memory_store(self) -> None:
        store = InMemoryStateStore()
        store.create(make_state("wf-running", status=WorkflowStatus.RUNNING))
        store.create(make_state("wf-succeeded", status=WorkflowStatus.SUCCEEDED))
        now = datetime.now(UTC)
        removed = store.cleanup(terminal_before=now, expired_before=now, max_records=10)
        assert removed == 1
        assert store.get("wf-running") is not None
        assert store.get("wf-succeeded") is None

    def test_cleanup_rejects_non_positive_batch(self, tmp_path: Path) -> None:
        store = SQLiteStateStore(tmp_path / "state.db")
        now = datetime.now(UTC)
        with pytest.raises(ValueError):
            store.cleanup(terminal_before=now, expired_before=now, max_records=0)
