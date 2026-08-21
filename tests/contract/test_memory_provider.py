"""Contract tests for the in-memory memory provider.

Covers append/recall, deterministic ordering, optimistic compare-and-set
conflicts, expiry, deletion, compaction, cross-tenant/conversation
isolation, budget enforcement, and normalized provider failure.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nl2data_core.canonical import canonical_json
from nl2data_core.memory.errors import MemoryErrorCode, MemoryInvocationError
from nl2data_core.memory.inmemory import InMemoryMemoryProvider
from nl2data_core.memory.models import (
    MemoryRecallBudget,
    MemoryRecord,
    MemoryScope,
    QueryReference,
    QueryReferencePayload,
    WorkingPayload,
)

FP = "sha256:" + "ab" * 32
FP2 = "sha256:" + "cd" * 32
NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def make_provider(**kwargs: object) -> InMemoryMemoryProvider:
    """Provider pinned to the fixed test clock so TTLs are deterministic."""
    return InMemoryMemoryProvider(clock=lambda: NOW, **kwargs)  # type: ignore[arg-type]


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
        payload=payload or QueryReferencePayload(
            reference=make_reference(reference_id=f"ref-{record_id}")
        ),
        created_at=created_at,
        ttl_seconds=ttl_seconds,
    )


class TestAppendRecall:
    def test_append_returns_stable_id_and_recall_returns_record(self) -> None:
        provider = make_provider()
        record = make_record("mem-1")
        assert provider.append(record) == "mem-1"
        projection = provider.recall(scope=make_scope())
        assert projection.record_count == 1
        assert projection.records[0].fingerprint == record.fingerprint
        assert projection.truncated is False

    def test_duplicate_append_rejected(self) -> None:
        provider = make_provider()
        provider.append(make_record("mem-1"))
        with pytest.raises(MemoryInvocationError) as exc:
            provider.append(make_record("mem-1"))
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED

    def test_capacity_exhaustion_raises_budget_error(self) -> None:
        provider = make_provider(max_records=2)
        provider.append(make_record("mem-1"))
        provider.append(make_record("mem-2"))
        with pytest.raises(MemoryInvocationError) as exc:
            provider.append(make_record("mem-3"))
        assert exc.value.code is MemoryErrorCode.BUDGET_EXCEEDED

    def test_recall_is_deterministic_order(self) -> None:
        provider = make_provider()
        provider.append(make_record("mem-b", created_at=NOW + timedelta(minutes=2)))
        provider.append(make_record("mem-a", created_at=NOW + timedelta(minutes=1)))
        provider.append(make_record("mem-c", created_at=NOW + timedelta(minutes=1)))
        projection = provider.recall(scope=make_scope())
        assert [record.record_id for record in projection.records] == [
            "mem-a",
            "mem-c",
            "mem-b",
        ]

    def test_expired_records_never_recalled(self) -> None:
        provider = make_provider()
        provider.append(make_record("mem-fresh"))
        provider.append(
            make_record("mem-stale", created_at=NOW, ttl_seconds=60)
        )
        projection = provider.recall(
            scope=make_scope(), now=NOW + timedelta(minutes=5)
        )
        assert [record.record_id for record in projection.records] == ["mem-fresh"]

    def test_budget_max_records_truncates(self) -> None:
        provider = make_provider()
        for index in range(5):
            provider.append(make_record(f"mem-{index}"))
        projection = provider.recall(
            scope=make_scope(), budget=MemoryRecallBudget(max_records=2)
        )
        assert projection.record_count == 2
        assert projection.truncated is True

    def test_budget_max_chars_truncates(self) -> None:
        provider = make_provider()
        record = make_record("mem-1")
        provider.append(record)
        provider.append(make_record("mem-2"))
        # A budget sized for exactly one record returns one and truncates.
        projection = provider.recall(
            scope=make_scope(),
            budget=MemoryRecallBudget(max_chars=len(canonical_json(record.safe_dump()))),
        )
        assert projection.record_count == 1
        assert projection.truncated is True

    def test_budget_max_tokens_truncates(self) -> None:
        provider = make_provider()
        provider.append(make_record("mem-1"))
        provider.append(make_record("mem-2"))
        projection = provider.recall(
            scope=make_scope(), budget=MemoryRecallBudget(max_tokens=8)
        )
        assert projection.record_count <= 2
        assert projection.token_estimate <= 8

    def test_recall_projection_safe_payload_has_fingerprints_only(self) -> None:
        provider = make_provider()
        provider.append(make_record("mem-1"))
        projection = provider.recall(scope=make_scope())
        payload = projection.safe_payload()
        assert payload["record_fingerprints"] == [projection.records[0].fingerprint]
        assert "payload" not in str(payload)


class TestScopeIsolation:
    def test_cross_tenant_isolation_fail_closed(self) -> None:
        provider = make_provider()
        provider.append(make_record("mem-tenant-a", scope=make_scope(tenant=FP)))
        projection = provider.recall(scope=make_scope(tenant=FP2))
        assert projection.record_count == 0
        # A tenant-unbound query never sees tenant-bound records either.
        projection = provider.recall(scope=make_scope(tenant=None))
        assert projection.record_count == 0

    def test_cross_session_isolation(self) -> None:
        provider = make_provider()
        provider.append(make_record("mem-s1", scope=make_scope(session="session-1")))
        projection = provider.recall(scope=make_scope(session="session-2"))
        assert projection.record_count == 0

    def test_cross_conversation_isolation(self) -> None:
        provider = make_provider()
        provider.append(
            make_record("mem-conv-a", scope=make_scope(conversation="conv-1"))
        )
        projection = provider.recall(scope=make_scope(conversation="conv-2"))
        assert projection.record_count == 0

    def test_unscoped_query_returns_all_sessions_of_tenant(self) -> None:
        provider = make_provider()
        provider.append(make_record("mem-1", scope=make_scope(conversation="conv-1")))
        provider.append(make_record("mem-2", scope=make_scope(conversation="conv-2")))
        projection = provider.recall(
            scope=make_scope(conversation=None, adapter=None, source=None)
        )
        assert projection.record_count == 2

    def test_working_memory_session_scoped_never_crosses_tenant_query(self) -> None:
        provider = make_provider()
        provider.append(
            MemoryRecord(
                record_id="mem-w",
                scope=MemoryScope(session_id="session-1"),
                payload=WorkingPayload(label="note"),
            )
        )
        projection = provider.recall(
            scope=MemoryScope(tenant_scope_fingerprint=FP, session_id="session-1")
        )
        assert projection.record_count == 0


class TestCompareAndSet:
    def test_cas_success_replaces_record(self) -> None:
        provider = make_provider()
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

    def test_cas_conflict_returns_false(self) -> None:
        provider = make_provider()
        original = make_record("mem-1")
        provider.append(original)
        # A stale expected fingerprint never replaces the stored record.
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

    def test_cas_id_mismatch_rejected(self) -> None:
        provider = make_provider()
        original = make_record("mem-1")
        provider.append(original)
        with pytest.raises(MemoryInvocationError) as exc:
            provider.compare_and_set(
                expected=original, replacement=make_record("mem-2")
            )
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED

    def test_cas_scope_mismatch_rejected(self) -> None:
        provider = make_provider()
        original = make_record("mem-1")
        provider.append(original)
        with pytest.raises(MemoryInvocationError) as exc:
            provider.compare_and_set(
                expected=original,
                replacement=make_record("mem-1", scope=make_scope(tenant=FP2)),
            )
        assert exc.value.code is MemoryErrorCode.SCOPE_MISMATCH


class TestExpiryDeletionCompaction:
    def test_expire_removes_record_and_reserves_id(self) -> None:
        provider = make_provider()
        provider.append(make_record("mem-1"))
        assert provider.expire("mem-1") is True
        assert provider.expire("mem-1") is False
        assert provider.recall(scope=make_scope()).record_count == 0
        with pytest.raises(MemoryInvocationError) as exc:
            provider.append(make_record("mem-1"))
        assert exc.value.code is MemoryErrorCode.RECORD_REJECTED

    def test_delete_removes_record_and_allows_reuse(self) -> None:
        provider = make_provider()
        provider.append(make_record("mem-1"))
        assert provider.delete("mem-1") is True
        assert provider.delete("mem-1") is False
        assert provider.recall(scope=make_scope()).record_count == 0
        assert provider.append(make_record("mem-1")) == "mem-1"

    def test_compact_removes_only_expired_records(self) -> None:
        provider = make_provider()
        provider.append(make_record("mem-fresh"))
        provider.append(make_record("mem-stale", ttl_seconds=60))
        provider.append(make_record("mem-tombstoned", ttl_seconds=60))
        provider.expire("mem-tombstoned")
        removed = provider.compact(now=NOW + timedelta(minutes=5))
        assert removed == 1  # only the time-expired record
        projection = provider.recall(scope=make_scope())
        assert [record.record_id for record in projection.records] == ["mem-fresh"]

    def test_clock_drives_expiry_when_now_omitted(self) -> None:
        clock_time = {"value": NOW}

        def clock() -> datetime:
            return clock_time["value"]

        provider = InMemoryMemoryProvider(clock=clock)
        provider.append(make_record("mem-1", ttl_seconds=60))
        clock_time["value"] = NOW + timedelta(hours=1)
        assert provider.recall(scope=make_scope()).record_count == 0
        assert provider.compact() == 1


class TestProviderFailure:
    def test_all_operations_fail_closed_when_unavailable(self) -> None:
        provider = make_provider(available=False)
        assert provider.is_available() is False
        with pytest.raises(MemoryInvocationError) as exc:
            provider.append(make_record("mem-1"))
        assert exc.value.code is MemoryErrorCode.MEMORY_UNAVAILABLE
        assert exc.value.retryable is True
        with pytest.raises(MemoryInvocationError):
            provider.recall(scope=make_scope())
        with pytest.raises(MemoryInvocationError):
            provider.compare_and_set(
                expected=make_record("mem-1"), replacement=make_record("mem-1")
            )
        with pytest.raises(MemoryInvocationError):
            provider.compact()
        with pytest.raises(MemoryInvocationError):
            provider.expire("mem-1")
        with pytest.raises(MemoryInvocationError):
            provider.delete("mem-1")
