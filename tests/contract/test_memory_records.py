"""Contract tests for immutable memory record and safe-reference models.

Covers immutability, raw payload rejection, deterministic fingerprints,
tenant/session scope binding, and bounded record/projection sizes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.memory.errors import (
    MemoryErrorCategory,
    MemoryErrorCode,
    MemoryErrorRecord,
    MemoryInvocationError,
    normalize_memory_error,
)
from nl2data_core.memory.models import (
    AuditReferencePayload,
    MemoryRecallBudget,
    MemoryRecallProjection,
    MemoryRecord,
    MemoryRecordKind,
    MemoryScope,
    QueryReference,
    QueryReferencePayload,
    SemanticDecision,
    SessionPayload,
    WorkingPayload,
    reject_raw_payload,
    scan_raw_text,
)

FP = "sha256:" + "ab" * 32
FP2 = "sha256:" + "cd" * 32
NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


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
        intent_fingerprint=FP,
        plan_fingerprint=FP,
        artifact_fingerprint=FP,
        policy_fingerprint=FP,
        catalog_fingerprint=FP,
        semantic_view_fingerprint=FP,
        adapter_id="sql",
        source_id="sales",
        root_entity_id="order",
        field_ids=frozenset({"order_id", "amount"}),
    )


def make_record(
    record_id: str = "mem-1",
    *,
    scope: MemoryScope | None = None,
    payload: QueryReferencePayload | None = None,
    ttl_seconds: int = 3600,
    created_at: datetime = NOW,
    metadata: dict[str, str] | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        record_id=record_id,
        scope=scope or make_scope(),
        payload=payload or QueryReferencePayload(reference=make_reference()),
        created_at=created_at,
        ttl_seconds=ttl_seconds,
        metadata=metadata or {},
    )


class TestMemoryRecordImmutability:
    def test_records_are_frozen(self) -> None:
        record = make_record()
        with pytest.raises(ValidationError):
            record.record_id = "other"  # type: ignore[misc]

    def test_payload_discriminator_derives_kind(self) -> None:
        assert make_record().kind is MemoryRecordKind.QUERY_REFERENCE
        working = MemoryRecord(
            record_id="mem-w",
            scope=MemoryScope(session_id="session-1"),
            payload=WorkingPayload(label="note", detail="compared totals"),
        )
        assert working.kind is MemoryRecordKind.WORKING
        session = MemoryRecord(
            record_id="mem-s",
            scope=make_scope(),
            payload=SessionPayload(session_summary="queried order totals"),
        )
        assert session.kind is MemoryRecordKind.SESSION
        audit = MemoryRecord(
            record_id="mem-a",
            scope=make_scope(),
            payload=AuditReferencePayload(audit_id="audit-1", event_fingerprint=FP),
        )
        assert audit.kind is MemoryRecordKind.AUDIT_REFERENCE

    def test_expires_at_derived_from_ttl(self) -> None:
        record = make_record(created_at=NOW, ttl_seconds=86_400)
        assert record.expires_at == NOW + timedelta(seconds=86_400)
        assert not record.is_expired(now=NOW + timedelta(hours=23))
        assert record.is_expired(now=NOW + timedelta(hours=25))

    def test_explicit_expires_at_validated(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRecord(
                record_id="mem-x",
                scope=make_scope(),
                payload=QueryReferencePayload(reference=make_reference()),
                created_at=NOW,
                expires_at=NOW,
            )


class TestScopeBinding:
    def test_non_working_requires_tenant_scope(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRecord(
                record_id="mem-no-tenant",
                scope=MemoryScope(session_id="session-1"),
                payload=QueryReferencePayload(reference=make_reference()),
            )

    def test_working_memory_may_be_session_scoped(self) -> None:
        record = MemoryRecord(
            record_id="mem-w",
            scope=MemoryScope(session_id="session-1"),
            payload=WorkingPayload(label="note"),
        )
        assert record.scope.tenant_scope_fingerprint is None

    def test_scope_fingerprint_is_deterministic_and_order_independent(self) -> None:
        a = MemoryScope(
            tenant_scope_fingerprint=FP,
            session_id="session-1",
            conversation_id="conv-1",
            adapter_id="sqlite",
            source_id="sales",
        )
        b = MemoryScope(
            source_id="sales",
            adapter_id="sqlite",
            conversation_id="conv-1",
            session_id="session-1",
            tenant_scope_fingerprint=FP,
        )
        assert a.fingerprint == b.fingerprint
        different = MemoryScope(
            tenant_scope_fingerprint=FP2,
            session_id="session-1",
            conversation_id="conv-1",
            adapter_id="sqlite",
            source_id="sales",
        )
        assert a.fingerprint != different.fingerprint

    def test_scope_safe_dump_has_no_raw_identifiers_beyond_scope(self) -> None:
        dumped = make_scope().safe_dump()
        assert dumped["tenant_scope_fingerprint"] == FP
        assert dumped["session_id"] == "session-1"
        assert set(dumped) == {
            "tenant_scope_fingerprint",
            "session_id",
            "conversation_id",
            "adapter_id",
            "source_id",
            "fingerprint",
        }


class TestRawPayloadRejection:
    def test_metadata_raw_key_names_rejected(self) -> None:
        for key in ("sql", "query", "prompt", "rows", "credentials", "token", "driver"):
            with pytest.raises(ValidationError):
                make_record(metadata={key: "anything"})

    def test_metadata_sql_shaped_value_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_record(metadata={"note": "select * from orders where id = 1"})

    def test_metadata_secret_marker_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_record(metadata={"note": "connection uses password=hunter2"})

    def test_metadata_bounds(self) -> None:
        with pytest.raises(ValidationError):
            make_record(metadata={f"key{i}": "v" for i in range(33)})
        with pytest.raises(ValidationError):
            make_record(metadata={"long": "x" * 257})

    def test_semantic_decision_rejects_sql(self) -> None:
        with pytest.raises(ValidationError):
            SemanticDecision(
                decision_id="d1",
                confirmed_interpretation="same as before: select * from orders",
                policy_fingerprint=FP,
                catalog_fingerprint=FP,
            )

    def test_semantic_decision_accepts_bounded_plain_text(self) -> None:
        decision = SemanticDecision(
            decision_id="d1",
            confirmed_interpretation="compare total order amounts by region",
            policy_fingerprint=FP,
            catalog_fingerprint=FP,
        )
        assert decision.fingerprint.startswith("sha256:")

    def test_reject_raw_payload_recursive(self) -> None:
        with pytest.raises(ValueError):
            reject_raw_payload({"nested": {"items": [{"sql": "select 1"}]}}, "root")

    def test_scan_raw_text_helpers(self) -> None:
        assert scan_raw_text("select * from orders where id = 1") == "executable_sql"
        assert scan_raw_text("api_key=abcd1234") == "secret_marker"
        assert scan_raw_text("compare totals by region") is None


class TestDeterministicFingerprints:
    def test_equal_records_produce_equal_fingerprints(self) -> None:
        a = make_record()
        b = make_record()
        assert a.fingerprint == b.fingerprint

    def test_fingerprint_changes_with_scope_and_payload(self) -> None:
        base = make_record()
        other_tenant = make_record(scope=make_scope(tenant=FP2))
        other_reference = make_record(
            payload=QueryReferencePayload(
                reference=make_reference(reference_id="ref-2")
            )
        )
        assert base.fingerprint != other_tenant.fingerprint
        assert base.fingerprint != other_reference.fingerprint

    def test_fingerprint_matches_canonical_form(self) -> None:
        record = make_record()
        expected = sha256_fingerprint(record.canonical_payload())
        assert record.fingerprint == expected

    def test_reference_fingerprint_deterministic(self) -> None:
        a = make_reference()
        b = make_reference()
        assert a.fingerprint == b.fingerprint
        assert a.fingerprint == sha256_fingerprint(a.canonical_payload())

    def test_record_ids_must_be_bounded_identifiers(self) -> None:
        with pytest.raises(ValidationError):
            make_record(record_id="not valid id!")
        with pytest.raises(ValidationError):
            make_record(record_id="x" * 129)


class TestBoundedSizes:
    def test_reference_field_ids_bounded(self) -> None:
        with pytest.raises(ValidationError):
            QueryReference(
                reference_id="ref-1",
                policy_fingerprint=FP,
                catalog_fingerprint=FP,
                source_id="sales",
                field_ids=frozenset({f"field-{i}" for i in range(257)}),
            )

    def test_ttl_bounds(self) -> None:
        with pytest.raises(ValidationError):
            make_record(ttl_seconds=0)
        with pytest.raises(ValidationError):
            make_record(ttl_seconds=3_153_601)

    def test_recall_budget_bounds(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRecallBudget(max_records=0)
        with pytest.raises(ValidationError):
            MemoryRecallBudget(max_tokens=0)

    def test_recall_projection_fingerprint_covers_references_only(self) -> None:
        record = make_record()
        projection = MemoryRecallProjection(
            scope_fingerprint=FP,
            records=(record,),
            char_count=120,
            token_estimate=30,
            truncated=True,
        )
        assert projection.record_count == 1
        assert projection.fingerprint == sha256_fingerprint(
            {
                "scope_fingerprint": FP,
                "records": [record.fingerprint],
                "truncated": True,
                "char_count": 120,
                "token_estimate": 30,
            }
        )
        payload = projection.safe_payload()
        assert payload["record_fingerprints"] == [record.fingerprint]
        assert "records" not in payload or all(
            isinstance(item, str) for item in payload["records"]
        )

    def test_projection_safe_payload_exposes_fingerprints_only(self) -> None:
        record = make_record()
        projection = MemoryRecallProjection(
            scope_fingerprint=FP, records=(record,), char_count=10, token_estimate=2
        )
        serialized = projection.safe_payload()
        assert serialized == {
            "scope_fingerprint": FP,
            "record_fingerprints": [record.fingerprint],
            "truncated": False,
            "char_count": 10,
            "token_estimate": 2,
            "fingerprint": projection.fingerprint,
        }


class TestMemoryErrorContracts:
    def test_unavailable_is_retryable_only(self) -> None:
        assert (
            MemoryInvocationError(MemoryErrorCode.MEMORY_UNAVAILABLE, "down").retryable
            is True
        )
        assert (
            MemoryInvocationError(
                MemoryErrorCode.SCOPE_MISMATCH, "scope mismatch"
            ).retryable
            is False
        )
        assert (
            MemoryInvocationError(
                MemoryErrorCode.RECORD_REJECTED, "rejected"
            ).retryable
            is False
        )
        assert (
            MemoryInvocationError(
                MemoryErrorCode.BUDGET_EXCEEDED, "budget"
            ).retryable
            is False
        )

    def test_record_is_frozen_and_redacted(self) -> None:
        record = MemoryInvocationError(
            MemoryErrorCode.RECORD_REJECTED,
            "rejected",
            details={"token": "super-secret", "count": "3"},
        ).to_record()
        assert record.code is MemoryErrorCode.RECORD_REJECTED
        assert record.category is MemoryErrorCategory.REQUEST
        assert record.details["token"] != "super-secret"
        assert record.details["count"] == "3"
        with pytest.raises(ValidationError):
            record.message = "changed"  # type: ignore[misc]

    def test_normalize_memory_error(self) -> None:
        normalized = normalize_memory_error(RuntimeError("provider exploded"))
        assert normalized.code is MemoryErrorCode.UNKNOWN_MEMORY_ERROR
        assert normalized.message == "<redacted>"
        assert isinstance(normalized, MemoryErrorRecord)
        assert normalized.safe_dump()["code"] == "UNKNOWN_MEMORY_ERROR"
