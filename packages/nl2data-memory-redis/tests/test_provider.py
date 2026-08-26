"""Unit/contract tests for the Redis shared memory provider.

Covers safe round trips, validated configuration, namespaced key isolation,
error normalization, TTL/retention behavior, recall budgets, injected fake
client behavior, and optimistic compare-and-set - all deterministic and free
of the optional ``redis`` driver.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from nl2data._redact import REDACTED_VALUE
from nl2data_core.canonical import canonical_json
from nl2data_core.memory.errors import MemoryErrorCode, MemoryInvocationError
from nl2data_core.memory.models import (
    MemoryRecallBudget,
    MemoryRecord,
    MemoryScope,
    QueryReference,
    QueryReferencePayload,
)
from nl2data_memory_redis import RedisMemoryConfig, RedisMemoryProvider
from nl2data_memory_redis.fake import FakeRedisClient
from nl2data_memory_redis.serialization import (
    SERIALIZATION_SCHEMA_VERSION,
    deserialize_record_value,
    serialize_record,
    serialize_tombstone,
)

FP = "sha256:" + "ab" * 32
FP2 = "sha256:" + "cd" * 32
NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
NAMESPACE = "ns"


def make_clock(start: datetime = NOW) -> tuple[dict[str, datetime], Callable[[], datetime]]:
    """A mutable clock shared by provider and fake so TTLs are deterministic."""
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
    created_at: datetime = NOW,
    ttl_seconds: int = 86_400,
) -> MemoryRecord:
    return MemoryRecord(
        record_id=record_id,
        scope=scope or make_scope(),
        payload=payload
        or QueryReferencePayload(reference=make_reference(reference_id=f"ref-{record_id}")),
        created_at=created_at,
        ttl_seconds=ttl_seconds,
    )


def make_provider(
    *,
    client: FakeRedisClient,
    clock: Callable[[], datetime] | None = None,
    namespace: str = NAMESPACE,
    **config_kwargs: object,
) -> RedisMemoryProvider:
    config = RedisMemoryConfig(namespace=namespace, **config_kwargs)
    return RedisMemoryProvider(config, client=client, clock=clock)


class TestSerialization:
    def test_round_trip_restores_validated_record(self) -> None:
        record = make_record("mem-1")
        restored = deserialize_record_value(serialize_record(record))
        assert restored is not None
        assert restored.fingerprint == record.fingerprint
        assert restored.record_id == record.record_id
        assert restored.scope.session_id == record.scope.session_id

    def test_envelope_is_versioned_and_deterministic(self) -> None:
        record = make_record("mem-1")
        first = serialize_record(record)
        assert serialize_record(record) == first
        envelope = json.loads(first)
        assert envelope["schema_version"] == SERIALIZATION_SCHEMA_VERSION
        assert envelope["record"]["kind"] == "query_reference"
        # The envelope carries the safe typed payload - never raw material.
        assert "payload" in envelope["record"]
        assert "raw" not in str(envelope["record"])
        assert "rows" not in str(envelope["record"])

    def test_tombstone_deserializes_to_none(self) -> None:
        assert deserialize_record_value(serialize_tombstone()) is None

    def test_malformed_json_rejected(self) -> None:
        with pytest.raises(MemoryInvocationError) as exc:
            deserialize_record_value("{not json")
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED
        assert exc.value.details["cause_type"] == "JSONDecodeError"

    def test_non_dict_envelope_rejected(self) -> None:
        with pytest.raises(MemoryInvocationError) as exc:
            deserialize_record_value("[1, 2]")
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED
        assert exc.value.details["cause_type"] == "envelope_shape"

    def test_missing_schema_version_rejected(self) -> None:
        with pytest.raises(MemoryInvocationError) as exc:
            deserialize_record_value('{"record": {}}')
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED

    def test_unsupported_schema_version_rejected(self) -> None:
        with pytest.raises(MemoryInvocationError) as exc:
            deserialize_record_value('{"schema_version": 2, "record": {}}')
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED
        assert exc.value.details["schema_version"] == "2"

    def test_unknown_envelope_fields_are_rejected(self) -> None:
        record = json.loads(serialize_record(make_record("mem-1")))
        record["raw_sql"] = "SELECT secret FROM orders"
        with pytest.raises(MemoryInvocationError) as exc:
            deserialize_record_value(json.dumps(record))
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED
        assert exc.value.details["cause_type"] == "envelope_fields"

    def test_non_dict_record_shape_rejected(self) -> None:
        with pytest.raises(MemoryInvocationError) as exc:
            deserialize_record_value('{"schema_version": 1, "record": "x"}')
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED
        assert exc.value.details["cause_type"] == "record_shape"

    def test_invalid_record_rejected(self) -> None:
        with pytest.raises(MemoryInvocationError) as exc:
            deserialize_record_value('{"schema_version": 1, "record": {"record_id": "x"}}')
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED
        assert exc.value.details["cause_type"] == "ValidationError"

    def test_raw_payload_never_accepted(self) -> None:
        safe = make_record("mem-1").safe_dump()
        safe["raw_sql"] = "SELECT secret FROM orders"
        with pytest.raises(MemoryInvocationError) as exc:
            deserialize_record_value(
                '{"schema_version": 1, "record": {}}'.replace("{}", json.dumps(safe))
            )
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED

    def test_stored_value_never_leaks_into_error(self) -> None:
        with pytest.raises(MemoryInvocationError) as exc:
            deserialize_record_value('{"schema_version": 1, "record": {"token": "supersecret"}}')
        assert "supersecret" not in str(exc.value)
        assert "supersecret" not in str(exc.value.details)


class TestConfigValidation:
    def test_defaults_are_bounded(self) -> None:
        config = RedisMemoryConfig(namespace="app-prod")
        assert config.namespace == "app-prod"
        assert config.max_ttl_seconds == 3_153_600
        assert config.max_records == 10_000
        assert config.expired_id_retention_seconds == 3_600

    def test_namespace_accepts_bounded_identifiers(self) -> None:
        RedisMemoryConfig(namespace="a")
        RedisMemoryConfig(namespace="9ns.v2-prod_x")

    @pytest.mark.parametrize(
        "namespace",
        [
            "",
            "has space",
            "colon:separated",
            "ümlaut",
            "x" * 65,
        ],
    )
    def test_namespace_pattern_rejects_unsafe_values(self, namespace: str) -> None:
        with pytest.raises(ValidationError):
            RedisMemoryConfig(namespace=namespace)

    @pytest.mark.parametrize(
        "field",
        [
            "max_ttl_seconds",
            "max_records",
            "max_candidates",
            "recall_batch_size",
            "compaction_batch_size",
            "expired_id_retention_seconds",
            "connect_timeout_seconds",
            "command_timeout_seconds",
        ],
    )
    def test_zero_bounds_rejected(self, field: str) -> None:
        with pytest.raises(ValidationError):
            RedisMemoryConfig(namespace="ns", **{field: 0})

    def test_oversized_bounds_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RedisMemoryConfig(namespace="ns", max_ttl_seconds=3_153_601)
        with pytest.raises(ValidationError):
            RedisMemoryConfig(namespace="ns", max_records=1_000_001)
        with pytest.raises(ValidationError):
            RedisMemoryConfig(namespace="ns", command_timeout_seconds=61.0)

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            RedisMemoryConfig(namespace="ns", url="redis://localhost:6379")

    def test_config_is_frozen(self) -> None:
        config = RedisMemoryConfig(namespace="ns")
        with pytest.raises(ValidationError):
            config.max_records = 5  # type: ignore[misc]


class TestKeyIsolation:
    def test_namespace_isolates_providers_sharing_one_backend(self) -> None:
        state, clock = make_clock()
        fake = FakeRedisClient(clock=clock)
        provider_a = make_provider(client=fake, clock=clock, namespace="ns-a")
        provider_b = make_provider(client=fake, clock=clock, namespace="ns-b")
        provider_a.append(make_record("mem-1"))
        assert provider_b.recall(scope=make_scope()).record_count == 0
        # The same id is free in the other namespace.
        assert provider_b.append(make_record("mem-1")) == "mem-1"

    def test_session_isolates_recall(self) -> None:
        state, clock = make_clock()
        provider = make_provider(client=FakeRedisClient(clock=clock), clock=clock)
        provider.append(make_record("mem-1", scope=make_scope(session="session-1")))
        assert provider.recall(scope=make_scope(session="session-2")).record_count == 0

    def test_tenant_isolates_recall(self) -> None:
        state, clock = make_clock()
        provider = make_provider(client=FakeRedisClient(clock=clock), clock=clock)
        provider.append(make_record("mem-1", scope=make_scope(tenant=FP)))
        assert provider.recall(scope=make_scope(tenant=FP2)).record_count == 0
        assert provider.recall(scope=make_scope(tenant=None)).record_count == 0

    def test_derived_keys_are_namespaced_and_bounded(self) -> None:
        state, clock = make_clock()
        fake = FakeRedisClient(clock=clock)
        provider = make_provider(client=fake, clock=clock)
        record = make_record("mem-1")
        provider.append(record)
        record_key = f"{NAMESPACE}:record:{FP}:session-1:mem-1"
        assert fake._values[f"{NAMESPACE}:ids:mem-1"] == record_key
        assert fake._sets[f"{NAMESPACE}:index:{FP}:session-1"] == {record_key}
        # A different session index stays empty.
        assert fake._sets.get(f"{NAMESPACE}:index:{FP}:session-2") in (None, set())

    @pytest.mark.parametrize("record_id", ["bad id!", "x" * 200, 123, "a:b"])
    def test_invalid_record_ids_rejected(self, record_id: object) -> None:
        state, clock = make_clock()
        provider = make_provider(client=FakeRedisClient(clock=clock), clock=clock)
        with pytest.raises(MemoryInvocationError) as exc:
            provider.expire(record_id)  # type: ignore[arg-type]
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED
        with pytest.raises(MemoryInvocationError):
            provider.delete(record_id)  # type: ignore[arg-type]


class TestErrorNormalization:
    def test_constructor_requires_url_or_client(self) -> None:
        with pytest.raises(MemoryInvocationError) as exc:
            RedisMemoryProvider(RedisMemoryConfig(namespace="ns"))
        assert exc.value.code is MemoryErrorCode.MEMORY_UNAVAILABLE
        assert exc.value.retryable is True

    def test_url_constructor_rejects_missing_driver(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "nl2data_memory_redis.provider.driver_available", lambda: False
        )
        with pytest.raises(MemoryInvocationError) as exc:
            RedisMemoryProvider(RedisMemoryConfig(namespace="ns"), url="redis://localhost:6379")
        assert exc.value.code is MemoryErrorCode.MEMORY_UNAVAILABLE
        assert exc.value.details["cause_type"] == "ImportError"

    def test_lazy_client_build_failure_is_normalized(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pretend the driver exists so construction passes; the real build
        # still fails lazily on first use and normalizes to unavailability.
        monkeypatch.setattr(
            "nl2data_memory_redis.provider.driver_available", lambda: True
        )
        provider = RedisMemoryProvider(
            RedisMemoryConfig(namespace="ns"), url="redis://localhost:6379"
        )
        assert provider.is_available() is False
        with pytest.raises(MemoryInvocationError) as exc:
            provider.append(make_record("mem-1"))
        assert exc.value.code is MemoryErrorCode.MEMORY_UNAVAILABLE

    def test_closed_provider_fails_closed(self) -> None:
        state, clock = make_clock()
        provider = make_provider(client=FakeRedisClient(clock=clock), clock=clock)
        provider.close()
        assert provider.is_available() is False
        with pytest.raises(MemoryInvocationError) as exc:
            provider.append(make_record("mem-1"))
        assert exc.value.code is MemoryErrorCode.MEMORY_UNAVAILABLE

    def test_connection_error_normalized(self) -> None:
        state, clock = make_clock()
        fake = FakeRedisClient(clock=clock)
        fake.close()
        provider = make_provider(client=fake, clock=clock)
        with pytest.raises(MemoryInvocationError) as exc:
            provider.recall(scope=make_scope())
        assert exc.value.code is MemoryErrorCode.MEMORY_UNAVAILABLE
        assert exc.value.retryable is True

    def test_driver_redis_error_normalized(self) -> None:
        class RedisError(Exception):
            pass

        provider = make_provider(client=_BrokenClient(RedisError("boom")))
        with pytest.raises(MemoryInvocationError) as exc:
            provider.append(make_record("mem-1"))
        assert exc.value.code is MemoryErrorCode.MEMORY_UNAVAILABLE
        assert exc.value.details["cause_type"] == "RedisError"

    def test_value_error_normalized_to_record_rejected(self) -> None:
        provider = make_provider(client=_BrokenClient(ValueError("bad value")))
        with pytest.raises(MemoryInvocationError) as exc:
            provider.append(make_record("mem-1"))
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED

    def test_unknown_error_redacted(self) -> None:
        provider = make_provider(client=_BrokenClient(RuntimeError("secret internals")))
        with pytest.raises(MemoryInvocationError) as exc:
            provider.append(make_record("mem-1"))
        assert exc.value.code is MemoryErrorCode.UNKNOWN_MEMORY_ERROR
        assert exc.value.message == REDACTED_VALUE
        assert "secret internals" not in str(exc.value)

    def test_lazy_exports_never_import_redis(self) -> None:
        import nl2data_memory_redis

        # Snapshot-diff so an earlier test that legitimately triggered the
        # lazy driver build (only possible when the extra is installed)
        # cannot cause a false negative: the exports themselves must add
        # nothing new to the process.
        before = {name.split(".")[0] for name in sys.modules}
        assert nl2data_memory_redis.RedisMemoryConfig is RedisMemoryConfig
        assert nl2data_memory_redis.RedisMemoryProvider is RedisMemoryProvider
        loaded = {name.split(".")[0] for name in sys.modules}
        assert loaded - before == set(), f"lazy exports imported: {loaded - before}"


class TestTtl:
    def test_ttl_above_provider_bound_rejected(self) -> None:
        state, clock = make_clock()
        provider = make_provider(
            client=FakeRedisClient(clock=clock), clock=clock, max_ttl_seconds=60
        )
        with pytest.raises(MemoryInvocationError) as exc:
            provider.append(make_record("mem-1", ttl_seconds=3_600))
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED
        assert exc.value.details["max_ttl_seconds"] == "60"

    def test_clock_drives_expiry_when_now_omitted(self) -> None:
        state, clock = make_clock()
        provider = make_provider(client=FakeRedisClient(clock=clock), clock=clock)
        provider.append(make_record("mem-1", ttl_seconds=60))
        state["now"] = NOW + timedelta(hours=1)
        assert provider.recall(scope=make_scope()).record_count == 0
        assert provider.compact() == 1

    def test_expired_records_never_recalled(self) -> None:
        state, clock = make_clock()
        provider = make_provider(client=FakeRedisClient(clock=clock), clock=clock)
        provider.append(make_record("mem-fresh"))
        provider.append(make_record("mem-stale", ttl_seconds=60))
        projection = provider.recall(scope=make_scope(), now=NOW + timedelta(minutes=5))
        assert [record.record_id for record in projection.records] == ["mem-fresh"]

    def test_expire_reserves_id_until_retention_window_passes(self) -> None:
        state, clock = make_clock()
        provider = make_provider(
            client=FakeRedisClient(clock=clock),
            clock=clock,
            expired_id_retention_seconds=60,
        )
        provider.append(make_record("mem-1", ttl_seconds=3_600))
        assert provider.expire("mem-1") is True
        assert provider.recall(scope=make_scope()).record_count == 0
        with pytest.raises(MemoryInvocationError) as exc:
            provider.append(make_record("mem-1"))
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED
        state["now"] = NOW + timedelta(seconds=120)
        assert provider.append(make_record("mem-1")) == "mem-1"


class TestBudgets:
    def test_candidate_cap_bounds_pathological_indexes(self) -> None:
        state, clock = make_clock()
        provider = make_provider(
            client=FakeRedisClient(clock=clock), clock=clock, max_candidates=2
        )
        for index in range(3):
            provider.append(make_record(f"mem-{index}"))
        projection = provider.recall(scope=make_scope())
        assert projection.record_count == 2
        assert projection.records[0].record_id != projection.records[1].record_id

    def test_max_records_budget_truncates(self) -> None:
        state, clock = make_clock()
        provider = make_provider(client=FakeRedisClient(clock=clock), clock=clock)
        for index in range(3):
            provider.append(make_record(f"mem-{index}"))
        projection = provider.recall(
            scope=make_scope(), budget=MemoryRecallBudget(max_records=2)
        )
        assert projection.record_count == 2
        assert projection.truncated is True

    def test_max_chars_budget_truncates(self) -> None:
        state, clock = make_clock()
        provider = make_provider(client=FakeRedisClient(clock=clock), clock=clock)
        record = make_record("mem-1")
        provider.append(record)
        provider.append(make_record("mem-2"))
        projection = provider.recall(
            scope=make_scope(),
            budget=MemoryRecallBudget(max_chars=len(canonical_json(record.safe_dump()))),
        )
        assert projection.record_count == 1
        assert projection.truncated is True

    def test_max_tokens_budget_truncates(self) -> None:
        state, clock = make_clock()
        provider = make_provider(client=FakeRedisClient(clock=clock), clock=clock)
        provider.append(make_record("mem-1"))
        provider.append(make_record("mem-2"))
        projection = provider.recall(
            scope=make_scope(), budget=MemoryRecallBudget(max_tokens=8)
        )
        assert projection.token_estimate <= 8


class TestInjectedClientBehavior:
    def test_append_duplicate_rejected_and_first_record_intact(self) -> None:
        state, clock = make_clock()
        provider = make_provider(client=FakeRedisClient(clock=clock), clock=clock)
        provider.append(make_record("mem-1"))
        with pytest.raises(MemoryInvocationError) as exc:
            provider.append(make_record("mem-1"))
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED
        assert provider.recall(scope=make_scope()).record_count == 1

    def test_capacity_enforced_against_session_index(self) -> None:
        state, clock = make_clock()
        provider = make_provider(
            client=FakeRedisClient(clock=clock), clock=clock, max_records=2
        )
        provider.append(make_record("mem-1"))
        provider.append(make_record("mem-2"))
        with pytest.raises(MemoryInvocationError) as exc:
            provider.append(make_record("mem-3"))
        assert exc.value.code is MemoryErrorCode.BUDGET_EXCEEDED

    def test_close_keeps_injected_client_open(self) -> None:
        state, clock = make_clock()
        fake = FakeRedisClient(clock=clock)
        provider = make_provider(client=fake, clock=clock)
        provider.close()
        assert fake.ping() is True

    def test_is_available_reflects_backend(self) -> None:
        state, clock = make_clock()
        fake = FakeRedisClient(clock=clock)
        provider = make_provider(client=fake, clock=clock)
        assert provider.is_available() is True
        fake.close()
        assert provider.is_available() is False

    def test_cas_success_replaces_record(self) -> None:
        state, clock = make_clock()
        provider = make_provider(client=FakeRedisClient(clock=clock), clock=clock)
        original = make_record("mem-1")
        provider.append(original)
        replacement = make_record(
            "mem-1",
            payload=QueryReferencePayload(
                reference=make_reference(reference_id="ref-updated")
            ),
        )
        assert provider.compare_and_set(expected=original, replacement=replacement) is True
        projection = provider.recall(scope=make_scope())
        assert projection.records[0].fingerprint == replacement.fingerprint

    def test_cas_stale_fingerprint_returns_false(self) -> None:
        state, clock = make_clock()
        provider = make_provider(client=FakeRedisClient(clock=clock), clock=clock)
        original = make_record("mem-1")
        provider.append(original)
        stale = make_record(
            "mem-1",
            payload=QueryReferencePayload(
                reference=make_reference(reference_id="ref-old")
            ),
        )
        replacement = make_record(
            "mem-1",
            payload=QueryReferencePayload(
                reference=make_reference(reference_id="ref-new")
            ),
        )
        assert provider.compare_and_set(expected=stale, replacement=replacement) is False
        projection = provider.recall(scope=make_scope())
        assert projection.records[0].fingerprint == original.fingerprint

    def test_cas_watch_conflict_returns_false(self) -> None:
        state, clock = make_clock()
        fake = FakeRedisClient(clock=clock)
        provider = make_provider(client=fake, clock=clock)
        original = make_record("mem-1")
        provider.append(original)
        replacement = make_record("mem-1")
        fake.fail_next_watch = True
        assert provider.compare_and_set(expected=original, replacement=replacement) is False

    def test_cas_missing_record_returns_false(self) -> None:
        state, clock = make_clock()
        provider = make_provider(client=FakeRedisClient(clock=clock), clock=clock)
        record = make_record("mem-1")
        assert provider.compare_and_set(expected=record, replacement=record) is False

    def test_cas_id_mismatch_rejected(self) -> None:
        state, clock = make_clock()
        provider = make_provider(client=FakeRedisClient(clock=clock), clock=clock)
        original = make_record("mem-1")
        provider.append(original)
        with pytest.raises(MemoryInvocationError) as exc:
            provider.compare_and_set(expected=original, replacement=make_record("mem-2"))
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED

    def test_cas_scope_mismatch_rejected(self) -> None:
        state, clock = make_clock()
        provider = make_provider(client=FakeRedisClient(clock=clock), clock=clock)
        original = make_record("mem-1")
        provider.append(original)
        with pytest.raises(MemoryInvocationError) as exc:
            provider.compare_and_set(
                expected=original,
                replacement=make_record("mem-1", scope=make_scope(tenant=FP2)),
            )
        assert exc.value.code is MemoryErrorCode.SCOPE_MISMATCH

    def test_cas_preserves_remaining_ttl(self) -> None:
        state, clock = make_clock()
        fake = FakeRedisClient(clock=clock)
        provider = make_provider(client=fake, clock=clock)
        original = make_record("mem-1", ttl_seconds=3_600)
        provider.append(original)
        state["now"] = NOW + timedelta(minutes=10)
        replacement = make_record("mem-1", ttl_seconds=3_600)
        assert provider.compare_and_set(expected=original, replacement=replacement) is True
        # The stored key keeps the remaining 3500s TTL, not a fresh 3600s.
        record_key = f"{NAMESPACE}:record:{FP}:session-1:mem-1"
        assert fake._expires_at[record_key] == NOW + timedelta(seconds=3_600)

    def test_delete_allows_immediate_id_reuse(self) -> None:
        state, clock = make_clock()
        provider = make_provider(client=FakeRedisClient(clock=clock), clock=clock)
        provider.append(make_record("mem-1"))
        assert provider.delete("mem-1") is True
        assert provider.delete("mem-1") is False
        assert provider.append(make_record("mem-1")) == "mem-1"

    def test_expire_delete_unknown_ids_return_false(self) -> None:
        state, clock = make_clock()
        provider = make_provider(client=FakeRedisClient(clock=clock), clock=clock)
        assert provider.expire("missing") is False
        assert provider.delete("missing") is False

    def test_expire_and_delete_retry_watched_conflicts(self) -> None:
        state, clock = make_clock()
        fake = FakeRedisClient(clock=clock)
        provider = make_provider(client=fake, clock=clock)
        provider.append(make_record("mem-expire"))
        fake.fail_next_watch = True
        assert provider.expire("mem-expire") is True
        assert provider.recall(scope=make_scope()).record_count == 0
        provider.append(make_record("mem-delete"))
        fake.fail_next_watch = True
        assert provider.delete("mem-delete") is True
        assert provider.recall(scope=make_scope()).record_count == 0

    def test_recall_scope_filters_after_index_lookup(self) -> None:
        state, clock = make_clock()
        fake = FakeRedisClient(clock=clock)
        provider = make_provider(client=fake, clock=clock)
        provider.append(make_record("mem-1"))
        # A foreign record planted in the shared index is never authorized.
        foreign_key = f"{NAMESPACE}:record:{FP2}:session-1:foreign"
        foreign = make_record("foreign", scope=make_scope(tenant=FP2))
        fake.set(foreign_key, serialize_record(foreign))
        fake.sadd(f"{NAMESPACE}:index:{FP}:session-1", foreign_key)
        projection = provider.recall(scope=make_scope())
        assert [record.record_id for record in projection.records] == ["mem-1"]

    def test_recall_skips_tombstones_and_dead_members(self) -> None:
        state, clock = make_clock()
        fake = FakeRedisClient(clock=clock)
        provider = make_provider(client=fake, clock=clock)
        provider.append(make_record("mem-1"))
        dead_key = f"{NAMESPACE}:record:{FP}:session-1:dead"
        fake.set(dead_key, serialize_tombstone())
        fake.sadd(f"{NAMESPACE}:index:{FP}:session-1", dead_key)
        projection = provider.recall(scope=make_scope())
        assert [record.record_id for record in projection.records] == ["mem-1"]


class TestCompaction:
    def test_compact_removes_stale_index_members(self) -> None:
        state, clock = make_clock()
        fake = FakeRedisClient(clock=clock)
        provider = make_provider(client=fake, clock=clock)
        provider.append(make_record("mem-fresh", ttl_seconds=3_600))
        provider.append(make_record("mem-stale", ttl_seconds=60))
        provider.append(make_record("mem-tombstoned", ttl_seconds=60))
        provider.expire("mem-tombstoned")
        dead_key = f"{NAMESPACE}:record:{FP}:session-1:ghost"
        fake.sadd(f"{NAMESPACE}:index:{FP}:session-1", dead_key)
        state["now"] = NOW + timedelta(minutes=5)
        assert provider.compact() == 3
        projection = provider.recall(scope=make_scope())
        assert [record.record_id for record in projection.records] == ["mem-fresh"]

    def test_compact_removes_malformed_values_as_stale(self) -> None:
        state, clock = make_clock()
        fake = FakeRedisClient(clock=clock)
        provider = make_provider(client=fake, clock=clock)
        provider.append(make_record("mem-1"))
        garbage_key = f"{NAMESPACE}:record:{FP}:session-1:garbage"
        fake.set(garbage_key, "{not json")
        fake.sadd(f"{NAMESPACE}:index:{FP}:session-1", garbage_key)
        assert provider.compact() == 1
        assert fake.get(garbage_key) is None
        assert provider.recall(scope=make_scope()).record_count == 1


class _BrokenClient:
    """Injected client stub that fails every provider command."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def ping(self) -> bool:
        return True

    def __getattr__(self, name: str) -> Any:
        def _fail(*args: object, **kwargs: object) -> Any:
            raise self._error

        return _fail
