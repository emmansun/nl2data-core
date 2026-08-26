"""Unit/contract tests for the nl2data-workflow-postgres package.

Covers configuration bounds, schema compatibility, safe snapshot
serialization, tenant scope namespaces, backend error normalization,
bounded cleanup, and core store-contract behavior - all exercised over the
deterministic in-memory fake pool.
"""

from __future__ import annotations

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from nl2data.errors import REDACTED_VALUE, ErrorCode
from nl2data_core.workflow.durable import (
    WorkflowSerializationError,
    deserialize_snapshot,
    serialize_snapshot,
    tenant_scope_namespace,
)
from nl2data_core.workflow.models import (
    WorkflowState,
    WorkflowStatus,
)
from nl2data_core.workflow.shared_errors import (
    SharedStoreError,
    SharedStoreErrorCode,
    normalize_shared_error,
)
from nl2data_core.workflow.transitions import transition
from pydantic import ValidationError

from nl2data_workflow_postgres import (
    PostgreSQLStateStore,
    WorkflowPostgresConfig,
)
from nl2data_workflow_postgres.fake_postgres import (
    FakePostgresPool,
    OperationalError,
    TimeoutError,
    UniqueViolation,
)
from nl2data_workflow_postgres.schema import MIGRATIONS, SUPPORTED_SCHEMA_VERSION
from nl2data_workflow_postgres.store import _BOOTSTRAP_DDL, SQL_TEMPLATES

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


def make_store(
    *, pool: FakePostgresPool | None = None
) -> tuple[PostgreSQLStateStore, FakePostgresPool]:
    pool = pool or FakePostgresPool()
    store = PostgreSQLStateStore(pool=pool, now=pool.clock.now)
    return store, pool


def make_terminal(
    workflow_id: str, status: WorkflowStatus = WorkflowStatus.SUCCEEDED
) -> WorkflowState:
    """A terminal snapshot reached through the valid transition chain."""
    state = make_state(workflow_id=workflow_id)
    for target in (WorkflowStatus.QUEUED, WorkflowStatus.RUNNING, status):
        state = transition(state, target, event_id=f"ev-{uuid4().hex[:8]}")
    return state


class TestWorkflowPostgresConfig:
    def test_defaults_are_valid_and_bounded(self) -> None:
        config = WorkflowPostgresConfig(namespace="shared")
        assert config.pool_size == 5
        assert config.schema_version == SUPPORTED_SCHEMA_VERSION
        assert config.lease_renewal_margin_seconds < config.lease_ttl_seconds
        assert config.clock_tolerance_seconds >= 0.0

    def test_namespace_must_be_a_safe_identifier(self) -> None:
        for bad in ("", "1shared", "shared-x", "shared.x", "shared x", "a" * 65):
            with pytest.raises(ValidationError):
                WorkflowPostgresConfig(namespace=bad)

    def test_pool_and_timeout_bounds_are_enforced(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowPostgresConfig(namespace="shared", pool_size=0)
        with pytest.raises(ValidationError):
            WorkflowPostgresConfig(namespace="shared", pool_size=65)
        with pytest.raises(ValidationError):
            WorkflowPostgresConfig(namespace="shared", connect_timeout_seconds=0.0)
        with pytest.raises(ValidationError):
            WorkflowPostgresConfig(namespace="shared", command_timeout_seconds=121.0)
        with pytest.raises(ValidationError):
            WorkflowPostgresConfig(namespace="shared", pool_acquire_timeout_seconds=61.0)

    def test_schema_version_is_bounded_by_support(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowPostgresConfig(namespace="shared", schema_version=0)
        with pytest.raises(ValidationError):
            WorkflowPostgresConfig(
                namespace="shared", schema_version=SUPPORTED_SCHEMA_VERSION + 1
            )

    def test_lease_timing_must_be_consistent(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowPostgresConfig(
                namespace="shared",
                lease_ttl_seconds=120.0,
                lease_renewal_margin_seconds=120.0,
            )
        with pytest.raises(ValidationError):
            WorkflowPostgresConfig(
                namespace="shared",
                lease_ttl_seconds=120.0,
                lease_renewal_margin_seconds=121.0,
            )

    def test_config_is_immutable_and_never_carries_dsn_or_credentials(self) -> None:
        config = WorkflowPostgresConfig(namespace="shared")
        with pytest.raises(ValidationError):
            config.namespace = "other"
        # Unknown fields are rejected by ``extra="forbid"`` in the validator.
        with pytest.raises(ValidationError):
            WorkflowPostgresConfig.model_validate(
                {"namespace": "shared", "dsn": "postgres://user:pass@host/db"}
            )
        for secret_name in ("dsn", "url", "password", "username", "ssl_cert"):
            assert secret_name not in WorkflowPostgresConfig.model_fields


class TestSchemaCompatibility:
    def test_schema_metadata_is_namespace_qualified(self) -> None:
        assert "{schema}.schema_metadata" in " ".join(
            (SQL_TEMPLATES["read_schema_version"], SQL_TEMPLATES["write_schema_version"])
        )
        assert "{schema}.schema_metadata" in _BOOTSTRAP_DDL

    def test_schema_constants_are_versioned_and_additive(self) -> None:
        assert SUPPORTED_SCHEMA_VERSION == 1
        assert set(MIGRATIONS) == {1}
        assert MIGRATIONS[1]  # every version ships at least one statement

    def test_fresh_store_initializes_schema_version_one(self) -> None:
        store, _ = make_store()
        assert store.schema_version() == SUPPORTED_SCHEMA_VERSION
        assert store.schema == "shared"

    def test_newer_schema_is_rejected_without_modification(self) -> None:
        pool = FakePostgresPool()
        pool.set_schema_version(99)
        with pytest.raises(SharedStoreError) as excinfo:
            PostgreSQLStateStore(pool=pool, now=pool.clock.now)
        error = excinfo.value
        assert error.code is SharedStoreErrorCode.SCHEMA_MISMATCH
        assert error.retryable is False
        assert error.is_public_rejected() is True
        # No migration was applied and the metadata was left untouched.
        assert pool.states == {}
        assert pool.idempotency == {}
        assert pool.leases == {}
        assert pool.schema_metadata["schema_version"] == "99"

    def test_existing_schema_is_reused_without_reapplying_migrations(self) -> None:
        store, pool = make_store()
        assert store.schema_version() == 1
        reopened = PostgreSQLStateStore(pool=pool, now=pool.clock.now)
        assert reopened.schema_version() == 1


class TestSerializationSafety:
    def test_snapshot_round_trips_exactly(self) -> None:
        state = make_state(status=WorkflowStatus.QUEUED, scope=SCOPE_A)
        assert deserialize_snapshot(serialize_snapshot(state)) == state

    def test_snapshot_contains_only_safe_fields(self) -> None:
        state = make_state(scope=SCOPE_A)
        payload = serialize_snapshot(state)
        document = json.loads(payload)
        assert set(document) == {"schema_version", "state"}
        # Raw prompts, queries, results, credentials, and DSN material never
        # appear anywhere in the durable snapshot.
        for forbidden in (
            "prompt",
            "password",
            "dsn",
            "postgres://",
            "secret",
            "SELECT",
        ):
            assert forbidden.lower() not in payload.lower()

    def test_malformed_stored_snapshot_is_a_structured_error(self) -> None:
        store, pool = make_store()
        store.create(make_state())
        pool.states[("", "wf-1")]["snapshot"] = "{not-json"
        with pytest.raises(WorkflowSerializationError) as excinfo:
            store.get("wf-1")
        assert excinfo.value.code is ErrorCode.INVALID_INPUT

    def test_unsupported_snapshot_version_is_rejected(self) -> None:
        store, pool = make_store()
        store.create(make_state())
        pool.states[("", "wf-1")]["snapshot"] = json.dumps(
            {"schema_version": 99, "state": {}}
        )
        with pytest.raises(WorkflowSerializationError) as excinfo:
            store.get("wf-1")
        assert excinfo.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION


class TestTenantNamespaces:
    def test_scope_namespace_derivation(self) -> None:
        assert tenant_scope_namespace(SCOPE_A) == f"tenant:workflow:{SCOPE_A}"
        assert tenant_scope_namespace(SCOPE_A, kind="memory") == f"tenant:memory:{SCOPE_A}"

    def test_state_is_visible_only_within_its_scope(self) -> None:
        store, _ = make_store()
        store.create(make_state(workflow_id="wf-a", scope=SCOPE_A))
        assert store.get("wf-a", tenant_scope_fingerprint=SCOPE_A) is not None
        assert store.get("wf-a", tenant_scope_fingerprint=SCOPE_B) is None
        assert store.get("wf-a") is None
        assert store.list_ids(tenant_scope_fingerprint=SCOPE_A) == ("wf-a",)
        assert store.list_ids(tenant_scope_fingerprint=SCOPE_B) == ()
        assert store.list_ids() == ()

    def test_idempotency_records_are_scope_isolated(self) -> None:
        store, _ = make_store()
        store.reserve_idempotency(
            "key-1",
            request_id="req-1",
            workflow_id="wf-a",
            tenant_scope_fingerprint=SCOPE_A,
        )
        assert store.get_idempotency("key-1", tenant_scope_fingerprint=SCOPE_A) is not None
        assert store.get_idempotency("key-1", tenant_scope_fingerprint=SCOPE_B) is None
        # The same key is free in another tenant scope.
        bound = store.reserve_idempotency(
            "key-1",
            request_id="req-2",
            workflow_id="wf-b",
            tenant_scope_fingerprint=SCOPE_B,
        )
        assert bound.request_id == "req-2"

    def test_lease_is_scope_isolated(self) -> None:
        store, _ = make_store()
        store.acquire_lease(
            "wf-a", owner_id="worker-a", tenant_scope_fingerprint=SCOPE_A, ttl_seconds=60.0
        )
        assert (
            store.inspect_lease("wf-a", tenant_scope_fingerprint=SCOPE_A) is not None
        )
        assert store.inspect_lease("wf-a", tenant_scope_fingerprint=SCOPE_B) is None
        # A different scope can acquire the same workflow id freely.
        taken = store.acquire_lease(
            "wf-a", owner_id="worker-b", tenant_scope_fingerprint=SCOPE_B, ttl_seconds=60.0
        )
        assert taken.fencing_token == 1


class TestErrorNormalization:
    def test_shared_error_passes_through(self) -> None:
        error = SharedStoreError(SharedStoreErrorCode.LEASE_BUSY, "busy")
        assert normalize_shared_error(error) is error

    def test_timeout_normalizes_to_retryable_store_timeout(self) -> None:
        error = normalize_shared_error(TimeoutError("statement timed out"))
        assert error.code is SharedStoreErrorCode.STORE_TIMEOUT
        assert error.retryable is True
        assert error.is_public_rejected() is False

    def test_connect_failure_normalizes_to_retryable_unavailable(self) -> None:
        error = normalize_shared_error(OperationalError("connection refused"))
        assert error.code is SharedStoreErrorCode.STORE_UNAVAILABLE
        assert error.retryable is True

    def test_duplicate_key_normalizes_to_state_conflict(self) -> None:
        error = normalize_shared_error(UniqueViolation("duplicate key"))
        assert error.code is SharedStoreErrorCode.STATE_CONFLICT
        assert error.retryable is False

    def test_unknown_error_is_redacted_but_retryable(self) -> None:
        error = normalize_shared_error(RuntimeError("internal server detail"))
        assert error.code is SharedStoreErrorCode.STORE_UNAVAILABLE
        assert error.retryable is True
        assert error.message == REDACTED_VALUE

    def test_public_record_mapping(self) -> None:
        mapping = {
            SharedStoreErrorCode.STORE_UNAVAILABLE: ErrorCode.STORE_UNAVAILABLE,
            SharedStoreErrorCode.STORE_TIMEOUT: ErrorCode.STORE_TIMEOUT,
            SharedStoreErrorCode.SCHEMA_MISMATCH: ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
            SharedStoreErrorCode.STATE_CONFLICT: ErrorCode.INVALID_TRANSITION,
            SharedStoreErrorCode.LEASE_BUSY: ErrorCode.LEASE_BUSY,
            SharedStoreErrorCode.FENCING_REJECTED: ErrorCode.FENCING_REJECTED,
        }
        rejected = {
            SharedStoreErrorCode.SCHEMA_MISMATCH,
            SharedStoreErrorCode.STATE_CONFLICT,
            SharedStoreErrorCode.LEASE_BUSY,
            SharedStoreErrorCode.FENCING_REJECTED,
        }
        for code, public_code in mapping.items():
            error = SharedStoreError(code, "detail-free message")
            record = error.to_public_record()
            assert record.code is public_code
            assert record.retryable == error.retryable
            assert record.cause_type == "SharedStoreError"
            assert error.is_public_rejected() == (code in rejected)

    def test_cause_details_never_leak_into_safe_dump(self) -> None:
        error = SharedStoreError(
            SharedStoreErrorCode.STORE_UNAVAILABLE,
            "unreachable",
            cause=OperationalError("postgres://user:password@secret-host:5432/db"),
        )
        dumped = error.to_record().safe_dump()
        assert "password" not in json.dumps(dumped)
        assert "secret-host" not in json.dumps(dumped)
        assert "postgres://" not in str(error)


class TestCleanup:
    def test_terminal_snapshot_is_removed_but_running_survives(self) -> None:
        store, pool = make_store()
        store.create(make_state(workflow_id="wf-done"))
        store.update(
            "wf-done", WorkflowStatus.CREATED, make_terminal("wf-done"), expected_version=1
        )
        store.create(make_state(workflow_id="wf-live", status=WorkflowStatus.QUEUED))
        pool.clock.advance(3_600.0)
        removed = store.cleanup(
            terminal_before=pool.clock.now(),
            expired_before=pool.clock.now(),
            max_records=10,
        )
        assert removed == 1
        assert store.get("wf-done") is None
        assert store.get("wf-live") is not None

    def test_expired_idempotency_is_removed_but_fresh_reserved_survives(self) -> None:
        store, pool = make_store()
        store.reserve_idempotency(
            "key-old",
            request_id="req-old",
            workflow_id="wf-old",
            expires_at=pool.clock.now() - timedelta(seconds=100),
        )
        store.reserve_idempotency(
            "key-new",
            request_id="req-new",
            workflow_id="wf-new",
            expires_at=pool.clock.now() + timedelta(seconds=3_600),
        )
        store.cleanup(
            terminal_before=pool.clock.now(),
            expired_before=pool.clock.now(),
            max_records=10,
        )
        assert store.get_idempotency("key-old") is None
        assert store.get_idempotency("key-new") is not None

    def test_expired_lease_is_removed_but_valid_lease_survives(self) -> None:
        store, pool = make_store()
        store.acquire_lease("wf-stale", owner_id="worker-a", ttl_seconds=120.0)
        pool.clock.advance(130.0)  # past TTL + clock tolerance
        # A lease acquired after the advance is still valid at cleanup.
        store.acquire_lease("wf-active", owner_id="worker-b", ttl_seconds=120.0)
        store.cleanup(
            terminal_before=pool.clock.now(),
            expired_before=pool.clock.now(),
            max_records=10,
        )
        assert store.inspect_lease("wf-stale") is None
        assert store.inspect_lease("wf-active") is not None

    def test_cleanup_is_bounded_per_pass(self) -> None:
        store, pool = make_store()
        for index in range(3):
            store.create(make_state(workflow_id=f"wf-{index}"))
            store.update(
                f"wf-{index}",
                WorkflowStatus.CREATED,
                make_terminal(f"wf-{index}"),
                expected_version=1,
            )
        pool.clock.advance(3_600.0)
        removed = store.cleanup(
            terminal_before=pool.clock.now(),
            expired_before=pool.clock.now(),
            max_records=1,
        )
        assert removed == 1
        remaining = [wf for wf in store.list_ids() if wf.startswith("wf-")]
        assert len(remaining) == 2
