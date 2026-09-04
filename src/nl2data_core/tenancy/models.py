"""Immutable trusted tenant-context models.

Contexts are created only from trusted host integration input; they can
never be established from public request bodies or prompts as an effective
authorization source.  Scope fingerprints are deterministic SHA-256
references and are never treated as authentication.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nl2data_core.canonical import strict_sha256_fingerprint

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class IsolationProfile(StrEnum):
    """Supported tenant isolation boundaries with explicit semantics."""

    POOLED = "pooled"
    SCHEMA_ISOLATED = "schema_isolated"
    DATABASE_ISOLATED = "database_isolated"
    DEPLOYMENT_ISOLATED = "deployment_isolated"


class IsolationProfileCapabilities(BaseModel):
    """Bounded capabilities of one isolation profile.

    A profile declares whether tenant-scoped execution is supported and
    the minimum enforcement obligations the host must guarantee; missing
    or unsupported profile data denies tenant-scoped execution instead of
    silently downgrading.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: IsolationProfile
    tenant_scoped_execution_supported: bool
    minimum_enforcement_obligations: tuple[str, ...] = Field(
        default_factory=tuple, max_length=16
    )


#: Registered isolation profiles with bounded capabilities.
ISOLATION_PROFILES: dict[IsolationProfile, IsolationProfileCapabilities] = {
    IsolationProfile.POOLED: IsolationProfileCapabilities(
        profile=IsolationProfile.POOLED,
        tenant_scoped_execution_supported=True,
        minimum_enforcement_obligations=(
            "every adapter statement must carry a verified tenant scope filter",
        ),
    ),
    IsolationProfile.SCHEMA_ISOLATED: IsolationProfileCapabilities(
        profile=IsolationProfile.SCHEMA_ISOLATED,
        tenant_scoped_execution_supported=True,
        minimum_enforcement_obligations=(
            "tenant schema routing must be verified before every adapter statement",
        ),
    ),
    IsolationProfile.DATABASE_ISOLATED: IsolationProfileCapabilities(
        profile=IsolationProfile.DATABASE_ISOLATED,
        tenant_scoped_execution_supported=True,
        minimum_enforcement_obligations=(
            "tenant database routing must be verified before every adapter connection",
        ),
    ),
    IsolationProfile.DEPLOYMENT_ISOLATED: IsolationProfileCapabilities(
        profile=IsolationProfile.DEPLOYMENT_ISOLATED,
        tenant_scoped_execution_supported=True,
        minimum_enforcement_obligations=(
            "tenant deployment boundary must be verified before every adapter connection",
        ),
    ),
}


class TenantLifecycleState(StrEnum):
    """Lifecycle state of a tenant isolation boundary."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    RETIRED = "retired"

    @property
    def active(self) -> bool:
        """Whether the tenant is currently allowed to execute queries."""
        return self is TenantLifecycleState.ACTIVE


class EntitlementRevision(BaseModel):
    """Bounded entitlement revision reference; never a policy claim list."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    revision_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    issued_at: datetime = Field(default_factory=_utc_now)


class Delegation(BaseModel):
    """Explicit delegation metadata: the delegating actor and approval."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    delegating_actor: str = Field(pattern=_IDENTIFIER_PATTERN)
    approved_at: datetime = Field(default_factory=_utc_now)
    approval_reference: str = Field(pattern=_IDENTIFIER_PATTERN)


class SubjectContext(BaseModel):
    """Effective principal scope with bounded roles and optional delegation.

    The delegating actor is recorded separately from the effective
    principal so delegated access can never be confused with direct
    access; both are bound into the scope fingerprint.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    principal_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    roles: frozenset[str] = Field(default_factory=frozenset, max_length=64)
    delegation: Delegation | None = None
    entitlement_revision: EntitlementRevision | None = None


class TenantContext(BaseModel):
    """Trusted tenant isolation boundary and lifecycle state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    environment: str = Field(pattern=_IDENTIFIER_PATTERN)
    isolation_profile: IsolationProfile
    lifecycle_state: TenantLifecycleState = TenantLifecycleState.ACTIVE
    enforcement_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)


def canonical_scope_payload(*, tenant: TenantContext, subject: SubjectContext) -> dict[str, Any]:
    """Canonical scope payload covering every fingerprint input.

    Includes tenant, effective principal, delegation actor, environment,
    isolation profile, roles, and entitlement revision.  The payload is
    order-independent through :func:`nl2data_core.canonical.sha256_fingerprint`.
    """
    delegation = subject.delegation
    revision = subject.entitlement_revision
    return {
        "tenant_id": tenant.tenant_id,
        "environment": tenant.environment,
        "isolation_profile": tenant.isolation_profile.value,
        "enforcement_fingerprint": tenant.enforcement_fingerprint,
        "principal_id": subject.principal_id,
        "roles": sorted(subject.roles),
        "delegation": (
            {
                "delegating_actor": delegation.delegating_actor,
                "approved_at": delegation.approved_at.isoformat(),
                "approval_reference": delegation.approval_reference,
            }
            if delegation is not None
            else None
        ),
        "entitlement_revision": (
            {
                "revision_id": revision.revision_id,
                "issued_at": revision.issued_at.isoformat(),
            }
            if revision is not None
            else None
        ),
    }


def scope_fingerprint(*, tenant: TenantContext, subject: SubjectContext) -> str:
    """Deterministic SHA-256 reference of the effective tenant scope.

    Equivalent scopes constructed in different insertion orders produce
    the same fingerprint; different tenants or principals never share one.
    A fingerprint is a stable reference, not proof of identity.
    """
    return strict_sha256_fingerprint(canonical_scope_payload(tenant=tenant, subject=subject))


class TenantScopeContext(BaseModel):
    """Immutable effective tenant scope created from trusted host input.

    The scope fingerprint is the safe reference that may cross governance,
    authorization, workflow, cache, and audit boundaries; raw tenant and
    principal identifiers never do.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: TenantContext
    subject: SubjectContext
    scope_fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> TenantScopeContext:
        object.__setattr__(
            self,
            "scope_fingerprint",
            scope_fingerprint(tenant=self.tenant, subject=self.subject),
        )
        return self

    @property
    def delegated(self) -> bool:
        """Whether the effective principal acts under an approved delegation."""
        return self.subject.delegation is not None

    def safe_dump(self) -> dict[str, Any]:
        """Public-safe serialization: fingerprints and profile metadata only.

        Raw tenant and principal identifiers, roles, delegation actors,
        and entitlement claims never appear in the output.
        """
        return {
            "scope_fingerprint": self.scope_fingerprint,
            "isolation_profile": self.tenant.isolation_profile.value,
            "lifecycle_state": self.tenant.lifecycle_state.value,
            "environment": self.tenant.environment,
            "delegated": self.delegated,
        }
