"""Tenant-scoped namespace and key primitives.

These produce deterministic, bounded references for workflow namespaces,
cache keys, and audit correlation.  Namespace strings carry only the
scope fingerprint - never raw tenant or principal identifiers - so they
are safe to persist and reuse across tenant-scoped records.
"""

from __future__ import annotations

import re

from .models import TenantScopeContext

_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _validate_component(value: str, name: str) -> str:
    if not _KEY_PATTERN.fullmatch(value):
        raise ValueError(f"{name} must be a bounded identifier-safe component")
    return value


def tenant_namespace(scope: TenantScopeContext, *, kind: str) -> str:
    """A deterministic tenant-scoped namespace string for a bounded kind.

    The namespace is bound to the effective scope fingerprint; equal
    scopes produce equal namespaces and different scopes never collide.
    """
    return f"tenant:{_validate_component(kind, 'kind')}:{scope.scope_fingerprint}"


def tenant_scoped_key(scope: TenantScopeContext, *, kind: str, key: str) -> str:
    """A deterministic tenant-scoped key within a bounded kind namespace."""
    _validate_component(key, "key")
    raw_identity = {
        scope.tenant.tenant_id,
        scope.subject.principal_id,
        *(  
            [scope.subject.delegation.delegating_actor]
            if scope.subject.delegation is not None
            else []
        ),
    }
    if key in raw_identity:
        raise ValueError("key must not contain a raw trusted identity")
    return f"{tenant_namespace(scope, kind=kind)}:{key}"
