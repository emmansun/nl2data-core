"""Admin contract and security tests for audit-evidence inspection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from nl2data_core.assembly import (
    MAX_TRAIL_ENTRIES,
    AssemblyAuditEvidenceEntry,
    AuditEventKind,
    AuditPayloadBindings,
    AuditSubjectKind,
    LifecycleRole,
    activation_audit_entry,
    draft_lifecycle_audit_entry,
    verification_reference_audit_entry,
)
from nl2data_core.assembly.lifecycle import DraftLifecycleAction, DraftLifecycleRecord
from nl2data_core.canonical import strict_sha256_fingerprint
from pydantic import ValidationError as PydanticValidationError

from helpers import _FakeDependencies, _make_auth
from nl2data_admin_service.auth import AuthContext, Permission
from nl2data_admin_service.config import AdminServiceConfig
from nl2data_admin_service.dtos import (
    AuditEntryView,
    AuditTrailPage,
    AuditTrailQuery,
    ImportAuthoringCommand,
)
from nl2data_admin_service.errors import (
    AuthorizationDeniedError,
    NotFoundError,
)
from nl2data_admin_service.schema import build_schema, validate_schema
from nl2data_admin_service.service import AdminService

_TENANT = "sha256:" + "0" * 64
_SOURCE_FP = strict_sha256_fingerprint({"source_id": "source-1"})
_EVIDENCE_FP = "sha256:" + "c" * 64
_BUNDLE_FP = "sha256:" + "d" * 64
_BASE = datetime(2026, 1, 1, tzinfo=UTC)


def document() -> str:
    return """apiVersion: nl2data.io/semantic-assembly-authoring/v1alpha1
kind: SemanticAssembly
metadata:
  bundleId: sales
  modelVersion: v1
spec:
  source:
    sourceId: source-1
  entities:
    - entityId: orders
      label: Orders
"""


def import_auth() -> AuthContext:
    return _make_auth(
        [Permission.ASSEMBLY_READ, Permission.ASSEMBLY_WRITE],
        lifecycle_roles=frozenset({LifecycleRole.AUTHOR}),
    )


def audit_auth() -> AuthContext:
    return _make_auth([Permission.ASSEMBLY_AUDIT])


def _imported_draft(service: AdminService) -> None:
    result = service.import_authoring(
        ImportAuthoringCommand(document=document(), draft_id="draft-1"),
        auth_context=import_auth(),
    )
    assert result.imported


def _verification_entry(index: int) -> AssemblyAuditEvidenceEntry:
    return verification_reference_audit_entry(
        tenant_scope_fingerprint=_TENANT,
        source_scope_fingerprint=_SOURCE_FP,
        event_id=f"verification-{index}",
        draft_id="draft-1",
        draft_revision=1 + index,
        evidence_fingerprint=_EVIDENCE_FP,
        policy_profile="admin-verification",
        policy_version=1,
        occurred_at=_BASE + timedelta(minutes=index),
    )


def _lifecycle_entry(index: int, *, reason: str = "") -> AssemblyAuditEvidenceEntry:
    record = DraftLifecycleRecord(
        draft_id="draft-1",
        action=DraftLifecycleAction.SUBMIT_FOR_REVIEW,
        operator_reference="author-1",
        previous_revision=index,
        resulting_revision=index + 1,
    )
    return draft_lifecycle_audit_entry(
        record,
        tenant_scope_fingerprint=_TENANT,
        source_scope_fingerprint=_SOURCE_FP,
        event_id=f"submit-{index}",
        occurred_at=_BASE + timedelta(minutes=index),
    )


def _seed_trail(dependencies: _FakeDependencies, count: int = 3) -> None:
    dependencies.lifecycle_catalog.record_audit_entries(
        [_verification_entry(index) for index in range(count)],
        tenant_scope_fingerprint=_TENANT,
    )


def _trail_length(dependencies: _FakeDependencies) -> int:
    return dependencies.lifecycle_catalog.audit_entries(
        tenant_scope_fingerprint=_TENANT,
    ).total_count


class TestAuditInspectionContract:
    def test_draft_trail_is_scoped_ordered_and_bounded(self) -> None:
        dependencies = _FakeDependencies()
        service = AdminService(dependencies, AdminServiceConfig())
        _imported_draft(service)
        _seed_trail(dependencies, count=3)
        result = service.inspect_audit_trail(
            AuditTrailQuery(draft_id="draft-1", limit=2),
            auth_context=audit_auth(),
        )
        assert isinstance(result, AuditTrailPage)
        assert [entry.event_id for entry in result.entries] == [
            "verification-0",
            "verification-1",
        ]
        assert result.total_count == 3
        assert result.has_more is True
        assert result.next_cursor == "verification-1"
        assert all(entry.draft_id == "draft-1" for entry in result.entries)

    def test_trail_paging_continues_from_cursor(self) -> None:
        dependencies = _FakeDependencies()
        service = AdminService(dependencies, AdminServiceConfig())
        _imported_draft(service)
        _seed_trail(dependencies, count=3)
        first = service.inspect_audit_trail(
            AuditTrailQuery(draft_id="draft-1", limit=2),
            auth_context=audit_auth(),
        )
        rest = service.inspect_audit_trail(
            AuditTrailQuery(draft_id="draft-1", cursor=first.next_cursor),
            auth_context=audit_auth(),
        )
        assert [entry.event_id for entry in rest.entries] == ["verification-2"]
        assert rest.has_more is False
        assert rest.next_cursor is None

    def test_revision_range_and_lifecycle_reference_filters(self) -> None:
        dependencies = _FakeDependencies()
        service = AdminService(dependencies, AdminServiceConfig())
        _imported_draft(service)
        catalog = dependencies.lifecycle_catalog
        catalog.record_audit_entries(
            [_lifecycle_entry(0), _verification_entry(1)],
            tenant_scope_fingerprint=_TENANT,
        )
        activation = _activation_entry()
        catalog.record_audit_entries([activation], tenant_scope_fingerprint=_TENANT)
        paged = service.inspect_audit_trail(
            AuditTrailQuery(draft_id="draft-1", draft_revision_min=2),
            auth_context=audit_auth(),
        )
        assert [entry.event_id for entry in paged.entries] == ["verification-1"]
        by_reference = service.inspect_audit_trail(
            AuditTrailQuery(lifecycle_reference="publish-1"),
            auth_context=audit_auth(),
        )
        assert [entry.event_id for entry in by_reference.entries] == [
            "activate-1",
        ]

    def test_unknown_subjects_return_bounded_empty_or_not_found(self) -> None:
        dependencies = _FakeDependencies()
        service = AdminService(dependencies, AdminServiceConfig())
        _imported_draft(service)
        _seed_trail(dependencies)
        with pytest.raises(NotFoundError):
            service.inspect_audit_trail(
                AuditTrailQuery(draft_id="draft-missing"),
                auth_context=audit_auth(),
            )
        empty = service.inspect_audit_trail(
            AuditTrailQuery(assertion_id="sha256:" + "e" * 64),
            auth_context=audit_auth(),
        )
        assert empty.entries == ()
        assert empty.total_count == 0
        assert empty.has_more is False


class TestAuditInspectionSecurity:
    def test_missing_permission_is_denied(self) -> None:
        dependencies = _FakeDependencies()
        service = AdminService(dependencies, AdminServiceConfig())
        _imported_draft(service)
        _seed_trail(dependencies)
        with pytest.raises(AuthorizationDeniedError):
            service.inspect_audit_trail(
                AuditTrailQuery(draft_id="draft-1"),
                auth_context=_make_auth([]),
            )

    def test_cross_source_trail_is_denied(self) -> None:
        dependencies = _FakeDependencies()
        service = AdminService(dependencies, AdminServiceConfig())
        _imported_draft(service)
        _seed_trail(dependencies)
        other_source = audit_auth().model_copy(
            update={"source_ids": frozenset({"source-2"})}
        )
        with pytest.raises(AuthorizationDeniedError):
            service.inspect_audit_trail(
                AuditTrailQuery(draft_id="draft-1"),
                auth_context=other_source,
            )

    def test_cross_source_bundle_fingerprint_query_is_denied(self) -> None:
        dependencies = _FakeDependencies()
        service = AdminService(dependencies, AdminServiceConfig())
        _imported_draft(service)
        dependencies.lifecycle_catalog.record_audit_entries(
            [_activation_entry()],
            tenant_scope_fingerprint=_TENANT,
        )
        other_source = audit_auth().model_copy(
            update={"source_ids": frozenset({"source-2"})}
        )
        with pytest.raises(AuthorizationDeniedError):
            service.inspect_audit_trail(
                AuditTrailQuery(bundle_fingerprint=_BUNDLE_FP),
                auth_context=other_source,
            )

    def test_cross_tenant_trail_is_isolated(self) -> None:
        dependencies = _FakeDependencies()
        service = AdminService(dependencies, AdminServiceConfig())
        _imported_draft(service)
        _seed_trail(dependencies)
        other_tenant = audit_auth().model_copy(
            update={"tenant_scope_fingerprint": "sha256:" + "9" * 64}
        )
        with pytest.raises(NotFoundError):
            service.inspect_audit_trail(
                AuditTrailQuery(draft_id="draft-1"),
                auth_context=other_tenant,
            )
        result = service.inspect_audit_trail(
            AuditTrailQuery(bundle_fingerprint=_BUNDLE_FP),
            auth_context=other_tenant,
        )
        assert result.entries == ()
        assert result.total_count == 0


class TestAuditInspectionSafety:
    def test_inspection_has_no_side_effects(self) -> None:
        dependencies = _FakeDependencies()
        service = AdminService(dependencies, AdminServiceConfig())
        _imported_draft(service)
        _seed_trail(dependencies, count=2)
        before = dependencies.lifecycle_catalog.audit_entries(
            tenant_scope_fingerprint=_TENANT
        )
        stored = dependencies.draft_store.get(
            "draft-1", tenant_scope_fingerprint=_TENANT
        )
        assert stored is not None
        for _ in range(3):
            service.inspect_audit_trail(
                AuditTrailQuery(draft_id="draft-1"),
                auth_context=audit_auth(),
            )
        after = dependencies.lifecycle_catalog.audit_entries(
            tenant_scope_fingerprint=_TENANT
        )
        assert after == before
        assert after.total_count == 2
        unchanged = dependencies.draft_store.get(
            "draft-1", tenant_scope_fingerprint=_TENANT
        )
        assert unchanged is not None
        assert unchanged.draft_revision == stored.draft_revision
        assert unchanged.state == stored.state

    def test_responses_are_redacted_and_scope_free(self) -> None:
        dependencies = _FakeDependencies()
        service = AdminService(dependencies, AdminServiceConfig())
        _imported_draft(service)
        long_reason = "x" * 300 + " confidential operator note"
        dependencies.lifecycle_catalog.record_audit_entries(
            [
                AssemblyAuditEvidenceEntry(
                    event_id="note-1",
                    event_kind=AuditEventKind.DRAFT_APPROVAL,
                    subject_kind=AuditSubjectKind.DRAFT,
                    subject_reference="draft-1",
                    tenant_scope_fingerprint=_TENANT,
                    source_scope_fingerprint=_SOURCE_FP,
                    draft_id="draft-1",
                    draft_revision=1,
                    operator_audit_reference="audit-1",
                    reason=long_reason,
                    payload_bindings=AuditPayloadBindings(),
                    occurred_at=_BASE,
                ),
            ],
            tenant_scope_fingerprint=_TENANT,
        )
        result = service.inspect_audit_trail(
            AuditTrailQuery(draft_id="draft-1"),
            auth_context=audit_auth(),
        )
        assert len(result.entries) == 1
        assert len(result.entries[0].reason) == 256
        assert result.entries[0].reason == "x" * 256
        serialized = result.model_dump_json()
        for forbidden in ("confidential operator note", "tenant_scope"):
            assert forbidden not in serialized
        view = result.entries[0]
        assert isinstance(view, AuditEntryView)
        assert "tenant_scope_fingerprint" not in view.model_dump()
        assert "source_scope_fingerprint" not in view.model_dump()

    def test_query_subject_and_bounds_are_validated(self) -> None:
        with pytest.raises(PydanticValidationError):
            AuditTrailQuery()
        with pytest.raises(PydanticValidationError):
            AuditTrailQuery(draft_revision_min=1)
        with pytest.raises(PydanticValidationError):
            AuditTrailQuery(draft_id="draft-1", draft_revision_min=3, draft_revision_max=1)
        with pytest.raises(PydanticValidationError):
            AuditTrailQuery(draft_id="draft-1", limit=0)
        with pytest.raises(PydanticValidationError):
            AuditTrailQuery(draft_id="draft-1", limit=MAX_TRAIL_ENTRIES + 1)
        service = AdminService(_FakeDependencies(), AdminServiceConfig())
        with pytest.raises(PydanticValidationError):
            service.inspect_audit_trail(
                AuditTrailQuery(draft_id="draft-1", limit=0),
                auth_context=audit_auth(),
            )


class TestAuditSchemaAndCapabilities:
    def test_schema_advertises_audit_inspection_dtos(self) -> None:
        schema = build_schema(AdminServiceConfig().contract_version)
        assert schema.commands["AuditTrailQuery"] is AuditTrailQuery
        assert schema.results["AuditEntryView"] is AuditEntryView
        assert schema.results["AuditTrailPage"] is AuditTrailPage
        assert validate_schema(schema) == []

    def test_capabilities_advertise_inspection_prerequisites(self) -> None:
        service = AdminService(_FakeDependencies(), AdminServiceConfig())
        capabilities = {
            capability.name: capability
            for capability in service.capabilities().capabilities
        }
        inspect = capabilities["assembly audit inspect"]
        assert inspect.permission is Permission.ASSEMBLY_AUDIT
        assert "draft_id" in inspect.subject_keys
        assert "bundle_fingerprint" in inspect.subject_keys
        assert inspect.maximum_result_count == MAX_TRAIL_ENTRIES
        assert inspect.cursor_paginated is True
        assert inspect.redacted is True

    def test_result_round_trips_through_json(self) -> None:
        dependencies = _FakeDependencies()
        service = AdminService(dependencies, AdminServiceConfig())
        _imported_draft(service)
        dependencies.lifecycle_catalog.record_audit_entries(
            [_verification_entry(0)],
            tenant_scope_fingerprint=_TENANT,
        )
        result = service.inspect_audit_trail(
            AuditTrailQuery(draft_id="draft-1"),
            auth_context=audit_auth(),
        )
        restored = AuditTrailPage.model_validate(result.model_dump(mode="json"))
        assert restored == result


def _activation_entry() -> AssemblyAuditEvidenceEntry:
    return activation_audit_entry(
        tenant_scope_fingerprint=_TENANT,
        source_scope_fingerprint=_SOURCE_FP,
        event_id="activate-1",
        bundle_fingerprint=_BUNDLE_FP,
        lifecycle_reference="publish-1",
        resulting_active_fingerprint=_BUNDLE_FP,
        occurred_at=_BASE + timedelta(minutes=9),
    )
