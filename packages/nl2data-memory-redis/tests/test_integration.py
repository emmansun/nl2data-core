"""Optional real Redis integration tests for nl2data-memory-redis.

Runs the shared memory provider against a real Redis service through two
lazily built clients, proving records, expiry, and deletion are durable
across connections. When the driver is missing, the URL is not configured,
or the service is unreachable the outcome is skipped - never a pass. Every run
uses a unique namespace with best-effort cleanup so runs never observe each
other's records.
"""

from __future__ import annotations

import os
import time
from uuid import uuid4

import pytest

from nl2data_core.memory.errors import MemoryErrorCode, MemoryInvocationError
from nl2data_core.memory.models import (
    MemoryRecord,
    MemoryScope,
    QueryReference,
    QueryReferencePayload,
)
from nl2data_memory_redis import RedisMemoryConfig, RedisMemoryProvider
from nl2data_memory_redis.client import build_redis_client, driver_available

#: Service location; override with NL2DATA_REDIS_URL for CI/dev services.
REDIS_URL = os.environ.get("NL2DATA_REDIS_URL", "redis://127.0.0.1:6379")

FP = "sha256:" + "ab" * 32


def _require_driver() -> None:
    """Skip cleanly when the optional driver is absent (skipped outcome)."""
    if not driver_available():
        pytest.skip("the redis driver is not installed; the real redis profile is skipped")


def _require_service() -> object:
    """Connect and ping, or skip; an unreachable service is never a pass."""
    _require_driver()
    client = build_redis_client(
        REDIS_URL, connect_timeout_seconds=2.0, command_timeout_seconds=2.0
    )
    try:
        client.ping()
        return client
    except Exception:
        pytest.skip("redis service is unavailable; the real redis profile is skipped")


def _cleanup(client: object, namespace: str) -> None:
    """Best-effort removal of this run's namespaced keys."""
    try:
        for key in client.scan_iter(match=f"{namespace}:*", count=500):
            client.delete(key)
    finally:
        client.close()


def make_scope() -> MemoryScope:
    return MemoryScope(
        tenant_scope_fingerprint=FP,
        session_id="session-1",
        conversation_id="conv-1",
        adapter_id="sqlite",
        source_id="sales",
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


def make_record(record_id: str, *, ttl_seconds: int = 86_400) -> MemoryRecord:
    # created_at defaults to the real wall clock so TTLs are live.
    return MemoryRecord(
        record_id=record_id,
        scope=make_scope(),
        payload=QueryReferencePayload(
            reference=make_reference(reference_id=f"ref-{record_id}")
        ),
        ttl_seconds=ttl_seconds,
    )


class TestRealRedisProfile:
    def test_driver_absence_is_skipped(self) -> None:
        """Without the optional driver the outcome is 'skipped', never a pass."""
        _require_driver()

    def test_append_recall_round_trip_across_connections(self) -> None:
        """A record appended through one client is recalled through another."""
        client = _require_service()
        namespace = f"nl2data-test-{uuid4().hex[:12]}"
        writer = RedisMemoryProvider(RedisMemoryConfig(namespace=namespace), url=REDIS_URL)
        reader = RedisMemoryProvider(RedisMemoryConfig(namespace=namespace), url=REDIS_URL)
        try:
            record = make_record("mem-1")
            assert writer.append(record) == "mem-1"
            projection = reader.recall(scope=record.scope)
            assert projection.record_count == 1
            assert projection.records[0].fingerprint == record.fingerprint
            assert projection.records[0].scope.session_id == "session-1"
            # Availability is observed through a separate connection too.
            assert reader.is_available() is True
        finally:
            writer.close()
            reader.close()
            _cleanup(client, namespace)

    def test_duplicate_append_rejected_across_connections(self) -> None:
        """Record-id uniqueness holds even across separate clients."""
        client = _require_service()
        namespace = f"nl2data-test-{uuid4().hex[:12]}"
        writer = RedisMemoryProvider(RedisMemoryConfig(namespace=namespace), url=REDIS_URL)
        reader = RedisMemoryProvider(RedisMemoryConfig(namespace=namespace), url=REDIS_URL)
        try:
            record = make_record("mem-1")
            assert writer.append(record) == "mem-1"
            with pytest.raises(MemoryInvocationError) as exc:
                reader.append(make_record("mem-1"))
            assert exc.value.code is MemoryErrorCode.RECORD_REJECTED
        finally:
            writer.close()
            reader.close()
            _cleanup(client, namespace)

    def test_expiry_and_delete_are_durable(self) -> None:
        """Natural TTL expiry and explicit deletion both hold on the service."""
        client = _require_service()
        namespace = f"nl2data-test-{uuid4().hex[:12]}"
        provider = RedisMemoryProvider(RedisMemoryConfig(namespace=namespace), url=REDIS_URL)
        try:
            short_lived = make_record("mem-1", ttl_seconds=2)
            provider.append(short_lived)
            time.sleep(3)
            assert provider.recall(scope=short_lived.scope).record_count == 0
            provider.append(make_record("mem-2"))
            assert provider.delete("mem-2") is True
            assert provider.recall(scope=short_lived.scope).record_count == 0
        finally:
            provider.close()
            _cleanup(client, namespace)

    def test_concurrent_append_preserves_uniqueness(self) -> None:
        """Concurrent appenders with the same id observe exactly one winner."""
        client = _require_service()
        namespace = f"nl2data-test-{uuid4().hex[:12]}"
        provider_a = RedisMemoryProvider(RedisMemoryConfig(namespace=namespace), url=REDIS_URL)
        provider_b = RedisMemoryProvider(RedisMemoryConfig(namespace=namespace), url=REDIS_URL)
        try:
            record = make_record("mem-1")
            results: list[bool | MemoryInvocationError] = []
            from threading import Thread

            def append(provider: RedisMemoryProvider) -> None:
                try:
                    provider.append(record)
                    results.append(True)
                except MemoryInvocationError as exc:
                    results.append(exc)

            t1 = Thread(target=append, args=(provider_a,))
            t2 = Thread(target=append, args=(provider_b,))
            t1.start()
            t2.start()
            t1.join()
            t2.join()
            assert sum(1 for r in results if r is True) == 1
            errors = [r for r in results if isinstance(r, MemoryInvocationError)]
            assert len(errors) == 1
            assert errors[0].code is MemoryErrorCode.RECORD_REJECTED
        finally:
            provider_a.close()
            provider_b.close()
            _cleanup(client, namespace)
