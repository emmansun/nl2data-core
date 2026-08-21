"""Trusted tenant-context boundary for tenant-scoped execution.

Contexts are created only from trusted host integration input and can
never be established from public request bodies or prompts.  Scope
fingerprints are deterministic SHA-256 references and are never treated
as authentication.
"""

from __future__ import annotations

from .models import (
    ISOLATION_PROFILES,
    Delegation,
    EntitlementRevision,
    IsolationProfile,
    IsolationProfileCapabilities,
    SubjectContext,
    TenantContext,
    TenantLifecycleState,
    TenantScopeContext,
    canonical_scope_payload,
    scope_fingerprint,
)
from .namespace import tenant_namespace, tenant_scoped_key
from .validation import (
    TenantContextError,
    TenantContextValidationResult,
    validate_tenant_scope,
)

__all__ = [
    "Delegation",
    "EntitlementRevision",
    "ISOLATION_PROFILES",
    "IsolationProfile",
    "IsolationProfileCapabilities",
    "SubjectContext",
    "TenantContext",
    "TenantContextError",
    "TenantContextValidationResult",
    "TenantLifecycleState",
    "TenantScopeContext",
    "canonical_scope_payload",
    "scope_fingerprint",
    "tenant_namespace",
    "tenant_scoped_key",
    "validate_tenant_scope",
]
