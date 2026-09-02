"""Audit-evidence inspection Admin capability service.

Inspection is side-effect-free: it only reads bounded, tenant/source
scoped, redacted audit-evidence trails from the lifecycle catalog and
never creates lifecycle events, review decisions, verification evidence,
publications, activations, rollbacks, or retention changes.
"""

from __future__ import annotations

from nl2data_core.assembly import AssemblyAuditEvidenceEntry, AuditTrail, redact_audit_entry
from nl2data_core.canonical import sha256_fingerprint

from .auth import AuthContext, Permission
from .common import AdminDependencyAccess, load_draft, require_permission
from .common import (
    normalize_errors as _normalize_errors,
)
from .config import AdminServiceConfig
from .dtos import AuditEntryView, AuditTrailPage, AuditTrailQuery
from .errors import AuthorizationDeniedError
from .protocols import AdminServiceDependencies


def _entry_view(entry: AssemblyAuditEvidenceEntry) -> AuditEntryView:
    """Project one entry into a redacted, scope-free inspection view."""
    payload = redact_audit_entry(entry)
    del payload["tenant_scope_fingerprint"]
    del payload["source_scope_fingerprint"]
    return AuditEntryView(**payload)


class AuditInspectionAdminCapability:
    """Bounded, scoped, redacted audit-evidence inspection operations."""

    def __init__(self, dependencies: AdminServiceDependencies, config: AdminServiceConfig) -> None:
        self._deps = dependencies
        self._access = AdminDependencyAccess(dependencies)
        self._config = config

    @_normalize_errors
    def inspect_audit_trail(
        self,
        query: AuditTrailQuery,
        *,
        auth_context: AuthContext,
    ) -> AuditTrailPage:
        """Return one deterministic bounded page of audit evidence."""
        require_permission(auth_context, Permission.ASSEMBLY_AUDIT)
        if query.draft_id is not None:
            # Resolves the subject within the caller's tenant scope and
            # enforces source authorization; unknown drafts fail safely.
            load_draft(self._access, query.draft_id, auth_context)
        catalog = self._access.lifecycle_catalog()
        trail: AuditTrail = catalog.audit_entries(
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
            draft_id=query.draft_id,
            draft_revision_min=query.draft_revision_min,
            draft_revision_max=query.draft_revision_max,
            assertion_id=query.assertion_id,
            bundle_fingerprint=query.bundle_fingerprint,
            lifecycle_reference=query.lifecycle_reference,
            predecessor_event_id=query.predecessor_event_id,
            limit=query.limit,
            cursor=query.cursor,
        )
        self._require_authorized_source_scope(trail.entries, auth_context)
        return AuditTrailPage(
            entries=tuple(_entry_view(entry) for entry in trail.entries),
            total_count=trail.total_count,
            next_cursor=trail.next_cursor,
            has_more=trail.has_more,
        )

    @staticmethod
    def _require_authorized_source_scope(
        entries: tuple[AssemblyAuditEvidenceEntry, ...],
        auth_context: AuthContext,
    ) -> None:
        """Fail closed when any entry falls outside the caller's source scope."""
        authorized = frozenset(
            sha256_fingerprint({"source_id": source_id})
            for source_id in auth_context.source_ids
        )
        if not authorized:
            return
        for entry in entries:
            if entry.source_scope_fingerprint not in authorized:
                raise AuthorizationDeniedError("Source not authorized")
