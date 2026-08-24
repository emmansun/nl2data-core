"""Cross-instance contract tests for the Redis shared memory provider.

Two provider instances share one fake Redis backend and one mutable clock,
proving the shared semantics the spec requires: replicas observe the same
scope, record-id uniqueness and compare-and-set are atomic across
instances, expiry/retention/compaction and budgets hold across instances,
and backend failures degrade through the same normalized errors.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from nl2data_core.memory.errors import MemoryErrorCode, MemoryInvocationError
from nl2data_core.memory.fake_redis import FakeRedisClient
from nl2data_core.memory.models import (
    MemoryRecallBudget,
    MemoryRecord,
    MemoryScope,
    QueryReference,
    QueryReferencePayload,
)
from nl2data_core.memory.redis_config import RedisMemoryConfig
from nl2data_core.memory.redis_provider import RedisMemoryProvider
from nl2data_core.memory.redis_serialization import serialize_record

FP = "sha256:" + "ab" * 32
FP2 = "sha256:" + "cd" * 32
NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
NAMESPACE = "shared-ns"


def make_clock(start: datetime = NOW) -> tuple[dict[str, datetime], Callable[[], datetime]]:
    """A mutable clock shared by both providers and the fake backend."""
    state = {"now": start}

    def clock() -> datetime:
        return state["now"]

    return state, clock


def make_scope(
    *,
    tenant: str | None = FP,
    session: str = "session-1",
    conversation: str | None = "conv-1",
    adapter: str | None = "sqlite",
    source: str | None = "sales",
) -> MemoryScope:
    return MemoryScope(
        tenant_scope_fingerprint=tenant,
        session_id=session,
        conversation_id=conversation,
        adapter_id=adapter,
        source_id=source,
    )


def make_reference(reference_id: str = "ref-1") -> QueryReference:
    return QueryReference(
        reference_id=reference_id,
        policy_fingerprint=FP,
        catalog_fingerprint=FP,
        semantic_view_fingerprint=FP,
        adapter_id="sql",
        source_id="sales",
        root_entity_id="order",
        field_ids=frozenset({"order_id", "amount"}),
    )


def make_record(
    record_id: str,
    *,
    scope: MemoryScope | None = None,
    payload: QueryReferencePayload | None = None,
    ttl_seconds: int = 86_400,
) -> MemoryRecord:
    return MemoryRecord(
        record_id=record_id,
        scope=scope or make_scope(),
        payload=payload
        or QueryReferencePayload(reference=make_reference(reference_id=f"ref-{record_id}")),
        created_at=NOW,
        ttl_seconds=ttl_seconds,
    )


def make_provider(
    client: FakeRedisClient,
    clock: Callable[[], datetime],
    namespace: str = NAMESPACE,
    **config_kwargs: object,
) -> RedisMemoryProvider:
    config = RedisMemoryConfig(namespace=namespace, **config_kwargs)
    return RedisMemoryProvider(config, client=client, clock=clock)


class TestReplicaObservation:
    def test_record_appended_through_one_instance_recalled_by_another(self) -> None:
        state, clock = make_clock()
        backend = FakeRedisClient(clock=clock)
        writer = make_provider(backend, clock)
        reader = make_provider(backend, clock)
        writer.append(make_record("mem-1"))
        projection = reader.recall(scope=make_scope())
        assert projection.record_count == 1
        # The recalled record preserves its protected fields and fingerprint.
        assert projection.records[0].fingerprint == make_record("mem-1").fingerprint
        assert projection.records[0].scope.session_id == "session-1"
        assert projection.records[0].kind.value == "query_reference"

    def test_provider_is_substitutable_across_operations(self) -> None:
        state, clock = make_clock()
        backend = FakeRedisClient(clock=clock)
        provider = make_provider(backend, clock)
        record = make_record("mem-1")
        assert provider.append(record) == "mem-1"
        assert provider.compare_and_set(expected=record, replacement=record) is True
        assert provider.expire("mem-1") is True
        assert provider.compact() == 1
        assert provider.delete("mem-1") is True
        assert provider.delete("mem-1") is False
        assert provider.is_available() is True

    def test_tenant_bound_memory_never_crosses_replicas(self) -> None:
        state, clock = make_clock()
        backend = FakeRedisClient(clock=clock)
        writer = make_provider(backend, clock)
        reader = make_provider(backend, clock)
        writer.append(make_record("mem-1", scope=make_scope(tenant=FP)))
        assert reader.recall(scope=make_scope(tenant=FP2)).record_count == 0
        # A missing tenant fingerprint never recalls tenant-bound records.
        assert reader.recall(scope=make_scope(tenant=None)).record_count == 0

    def test_session_isolation_across_replicas(self) -> None:
        state, clock = make_clock()
        backend = FakeRedisClient(clock=clock)
        writer = make_provider(backend, clock)
        reader = make_provider(backend, clock)
        writer.append(make_record("mem-1", scope=make_scope(session="session-1")))
        assert reader.recall(scope=make_scope(session="session-2")).record_count == 0


class TestAtomicMutations:
    def test_concurrent_append_preserves_uniqueness(self) -> None:
        state, clock = make_clock()
        backend = FakeRedisClient(clock=clock)
        instance_a = make_provider(backend, clock)
        instance_b = make_provider(backend, clock)
        assert instance_a.append(make_record("mem-1")) == "mem-1"
        with pytest.raises(MemoryInvocationError) as exc:
            instance_b.append(make_record("mem-1"))
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED
        # Exactly one record exists under the shared id.
        assert instance_a.recall(scope=make_scope()).record_count == 1
        assert instance_b.recall(scope=make_scope()).record_count == 1

    def test_append_capacity_is_checked_inside_the_transaction(self) -> None:
        state, clock = make_clock()
        backend = FakeRedisClient(clock=clock)
        instance_a = make_provider(backend, clock, max_records=1)
        instance_b = make_provider(backend, clock, max_records=1)
        instance_a.append(make_record("mem-1"))
        with pytest.raises(MemoryInvocationError) as exc:
            instance_b.append(make_record("mem-2"))
        assert exc.value.code is MemoryErrorCode.BUDGET_EXCEEDED
        assert instance_a.recall(scope=make_scope()).record_count == 1

    def test_stale_compare_and_set_cannot_overwrite(self) -> None:
        state, clock = make_clock()
        backend = FakeRedisClient(clock=clock)
        instance_a = make_provider(backend, clock)
        instance_b = make_provider(backend, clock)
        original = make_record("mem-1")
        instance_a.append(original)
        winner = make_record(
            "mem-1",
            payload=QueryReferencePayload(
                reference=make_reference(reference_id="ref-winner")
            ),
        )
        loser = make_record(
            "mem-1",
            payload=QueryReferencePayload(
                reference=make_reference(reference_id="ref-loser")
            ),
        )
        assert instance_a.compare_and_set(expected=original, replacement=winner) is True
        # The other instance's stale attempt is refused, not applied.
        assert instance_b.compare_and_set(expected=original, replacement=loser) is False
        projection = instance_b.recall(scope=make_scope())
        assert projection.records[0].fingerprint == winner.fingerprint

    def test_expiry_through_one_instance_reserves_id_for_another(self) -> None:
        state, clock = make_clock()
        backend = FakeRedisClient(clock=clock)
        instance_a = make_provider(backend, clock)
        instance_b = make_provider(backend, clock)
        instance_a.append(make_record("mem-1", ttl_seconds=3_600))
        assert instance_b.expire("mem-1") is True
        assert instance_a.recall(scope=make_scope()).record_count == 0
        with pytest.raises(MemoryInvocationError) as exc:
            instance_a.append(make_record("mem-1"))
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED

    def test_deletion_through_one_instance_allows_reuse_by_another(self) -> None:
        state, clock = make_clock()
        backend = FakeRedisClient(clock=clock)
        instance_a = make_provider(backend, clock)
        instance_b = make_provider(backend, clock)
        instance_a.append(make_record("mem-1"))
        assert instance_b.delete("mem-1") is True
        assert instance_a.append(make_record("mem-1")) == "mem-1"


class TestDurableExpiryAndBudgets:
    def test_expired_record_not_recalled_by_another_instance(self) -> None:
        state, clock = make_clock()
        backend = FakeRedisClient(clock=clock)
        instance_a = make_provider(backend, clock)
        instance_b = make_provider(backend, clock)
        instance_a.append(make_record("mem-stale", ttl_seconds=60))
        instance_a.append(make_record("mem-fresh"))
        state["now"] = NOW + timedelta(minutes=5)
        projection = instance_b.recall(scope=make_scope())
        assert [record.record_id for record in projection.records] == ["mem-fresh"]
        # Compaction through the other instance removes the expired storage.
        assert instance_b.compact() == 1
        assert instance_b.recall(scope=make_scope()).record_count == 1

    def test_recall_budget_enforced_across_instances(self) -> None:
        state, clock = make_clock()
        backend = FakeRedisClient(clock=clock)
        instance_a = make_provider(backend, clock)
        instance_b = make_provider(backend, clock)
        for index in range(3):
            instance_a.append(make_record(f"mem-{index}"))
        projection = instance_b.recall(
            scope=make_scope(), budget=MemoryRecallBudget(max_records=2)
        )
        assert projection.record_count == 2
        assert projection.truncated is True

    def test_incompatible_stored_value_reported_and_never_recalled(self) -> None:
        state, clock = make_clock()
        backend = FakeRedisClient(clock=clock)
        instance_a = make_provider(backend, clock)
        instance_b = make_provider(backend, clock)
        instance_a.append(make_record("mem-1"))
        # A foreign process writes a value that fails validation.
        backend.set(
            f"{NAMESPACE}:record:{FP}:session-1:corrupt",
            '{"schema_version": 1, "record": {"record_id": "corrupt", "raw_sql": "x"}}',
        )
        backend.sadd(
            f"{NAMESPACE}:index:{FP}:session-1",
            f"{NAMESPACE}:record:{FP}:session-1:corrupt",
        )
        with pytest.raises(MemoryInvocationError) as exc:
            instance_b.recall(scope=make_scope())
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED
        assert "corrupt" not in str(exc.value)
        # Compaction treats the incompatible value as stale and restores recall.
        assert instance_b.compact() == 1
        assert instance_b.recall(scope=make_scope()).record_count == 1

    def test_stale_index_members_tolerated_by_recall(self) -> None:
        state, clock = make_clock()
        backend = FakeRedisClient(clock=clock)
        instance_a = make_provider(backend, clock)
        instance_b = make_provider(backend, clock)
        instance_a.append(make_record("mem-1"))
        # A dead member (record key already gone) in the shared index.
        backend.sadd(
            f"{NAMESPACE}:index:{FP}:session-1",
            f"{NAMESPACE}:record:{FP}:session-1:ghost",
        )
        projection = instance_b.recall(scope=make_scope())
        assert [record.record_id for record in projection.records] == ["mem-1"]


class TestBackendFailure:
    def test_outage_raises_normalized_unavailability(self) -> None:
        state, clock = make_clock()
        backend = FakeRedisClient(clock=clock)
        provider = make_provider(backend, clock)
        backend.close()
        with pytest.raises(MemoryInvocationError) as exc:
            provider.append(make_record("mem-1"))
        assert exc.value.code is MemoryErrorCode.MEMORY_UNAVAILABLE
        assert exc.value.retryable is True
        error_record = exc.value.to_record()
        assert "redis://" not in str(error_record)
        assert "password" not in str(error_record)
        assert "6379" not in str(error_record)

    def test_health_check_fails_closed_without_leaking_configuration(self) -> None:
        state, clock = make_clock()
        backend = FakeRedisClient(clock=clock)
        provider = make_provider(backend, clock)
        assert provider.is_available() is True
        backend.close()
        assert provider.is_available() is False
        # is_available never raises and never exposes backend details.
        assert "redis://" not in repr(provider)

    def test_fingerprint_protection_across_instances(self) -> None:
        state, clock = make_clock()
        backend = FakeRedisClient(clock=clock)
        instance_a = make_provider(backend, clock)
        instance_b = make_provider(backend, clock)
        record = make_record("mem-1")
        instance_a.append(record)
        projection = instance_b.recall(scope=make_scope())
        safe = projection.safe_payload()
        assert safe["record_fingerprints"] == [record.fingerprint]
        assert "payload" not in str(safe)

    def test_provider_ignores_records_outside_its_namespace(self) -> None:
        state, clock = make_clock()
        backend = FakeRedisClient(clock=clock)
        other_namespace = make_provider(backend, clock, namespace="other-ns")
        provider = make_provider(backend, clock)
        other_namespace.append(make_record("mem-1"))
        assert provider.recall(scope=make_scope()).record_count == 0
        assert provider.append(make_record("mem-1")) == "mem-1"

    def test_raw_payload_never_stored_through_shared_provider(self) -> None:
        state, clock = make_clock()
        backend = FakeRedisClient(clock=clock)
        provider = make_provider(backend, clock)
        record = make_record("mem-1")
        provider.append(record)
        safe = record.safe_dump()
        safe["raw_sql"] = "SELECT 1 FROM orders"
        with pytest.raises(ValidationError):
            MemoryRecord.model_validate(safe)
        # Only validated safe representations ever reach the backend.
        stored = [value for value in backend._values.values() if value.startswith("{")]
        assert stored == [serialize_record(record)]
