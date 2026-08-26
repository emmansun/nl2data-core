"""Security tests for the Redis memory provider boundary.

Proves that raw prompts/queries/results, credentials, native objects,
Redis URLs, and malformed stored values never leak into recalled memory
or across error messages.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from nl2data_core.memory.errors import MemoryErrorCode, MemoryInvocationError
from nl2data_core.memory.models import (
    MemoryRecord,
    MemoryScope,
    QueryReference,
    QueryReferencePayload,
    WorkingPayload,
)
from nl2data_memory_redis import RedisMemoryConfig, RedisMemoryProvider
from nl2data_memory_redis.fake import FakeRedisClient
from nl2data_memory_redis.serialization import serialize_record

FP = "sha256:" + "ab" * 32
NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def _scope() -> MemoryScope:
    return MemoryScope(
        tenant_scope_fingerprint=FP,
        session_id="session-1",
        conversation_id="conv-1",
        adapter_id="sqlite",
        source_id="sales",
    )


def _reference(reference_id: str = "ref-1") -> QueryReference:
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


def _record(record_id: str, **kwargs: object) -> MemoryRecord:
    return MemoryRecord(
        record_id=record_id,
        scope=_scope(),
        payload=QueryReferencePayload(reference=_reference(reference_id=f"ref-{record_id}")),
        created_at=NOW,
        ttl_seconds=86_400,
        **kwargs,
    )


class TestRawPayloadBoundary:
    def test_prompt_payload_is_rejected_at_model_boundary(self) -> None:
        """A working-memory note that looks like a raw prompt is rejected."""
        with pytest.raises(ValueError):
            MemoryRecord(
                record_id="mem-1",
                scope=_scope(),
                payload=WorkingPayload(label="prompt", detail="SELECT * FROM orders"),
            )

    def test_query_reference_payload_never_contains_query_text(self) -> None:
        """References carry only fingerprints, not the original query."""
        record = _record("mem-1")
        dumped = record.safe_dump()
        text = json.dumps(dumped)
        assert "SELECT" not in text.upper()
        assert "FROM" not in text.upper()
        assert "orders" not in text

    def test_metadata_with_raw_payload_keys_is_rejected(self) -> None:
        """Record metadata containing raw payload key names is rejected."""
        with pytest.raises(ValueError):
            MemoryRecord(
                record_id="mem-1",
                scope=_scope(),
                payload=QueryReferencePayload(reference=_reference()),
                metadata={"raw_sql": "SELECT * FROM orders"},
            )


class TestCredentialAndUrlBoundary:
    def test_redis_url_never_appears_in_error_messages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "nl2data_memory_redis.provider.driver_available", lambda: True
        )
        provider = RedisMemoryProvider(
            RedisMemoryConfig(namespace="ns"),
            url="redis://user:secret@localhost:6379/0",
        )
        with pytest.raises(MemoryInvocationError) as exc:
            provider.append(_record("mem-1"))
        assert "redis://" not in str(exc.value)
        assert "secret" not in str(exc.value)
        assert exc.value.code is MemoryErrorCode.MEMORY_UNAVAILABLE

    def test_redis_password_never_appears_in_error_details(self) -> None:
        provider = RedisMemoryProvider(
            RedisMemoryConfig(namespace="ns"), client=FakeRedisClient()
        )
        # A malformed stored value with a password key is rejected but the
        # password text must not leak into the error details.
        bad = '{"schema_version": 1, "record": {"password": "supersecret"}}'
        error = provider._normalize_error(ValueError(bad), operation="recall")  # type: ignore[misc]
        assert "supersecret" not in str(error)
        assert "supersecret" not in str(error.details)


class TestNativeObjectBoundary:
    def test_native_objects_are_never_accepted_as_records(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRecord(
                record_id="mem-1",
                scope=_scope(),
                payload=object(),  # type: ignore[arg-type]
            )


class TestMalformedStoredValueBoundary:
    def _record_key(self, record_id: str) -> str:
        tenant = FP or "global"
        return f"ns:record:{tenant}:session-1:{record_id}"

    def test_malformed_value_is_never_returned_as_memory(self) -> None:
        fake = FakeRedisClient()
        provider = RedisMemoryProvider(RedisMemoryConfig(namespace="ns"), client=fake)
        valid = _record("mem-1")
        provider.append(valid)
        # Corrupt the stored envelope so it is malformed but still a string.
        corrupted = '{"schema_version": 1, "record": {"token": "leaked"}}'
        key = self._record_key("mem-1")
        fake._values[key] = corrupted
        # Recall must not surface the malformed value; it raises a bounded
        # data error and never returns the value as a record.
        with pytest.raises(MemoryInvocationError) as exc:
            provider.recall(scope=_scope())
        assert "leaked" not in str(exc.value)
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED

    def test_unknown_schema_version_is_rejected(self) -> None:
        fake = FakeRedisClient()
        provider = RedisMemoryProvider(RedisMemoryConfig(namespace="ns"), client=fake)
        valid = _record("mem-1")
        provider.append(valid)
        key = self._record_key("mem-1")
        fake._values[key] = '{"schema_version": 99, "record": {}}'
        with pytest.raises(MemoryInvocationError) as exc:
            provider.recall(scope=_scope())
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED
