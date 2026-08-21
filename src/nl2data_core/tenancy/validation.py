"""Fail-closed validation for trusted tenant context.

Validation is pure and side-effect free: a trusted context plus an
optional untrusted client hint in, a typed result out.  Missing, inactive,
conflicting, unknown, or unsupported tenant scope is denied before any
tenant-scoped execution; a client hint is never authority on its own.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError

from .models import ISOLATION_PROFILES, TenantScopeContext


class TenantContextError(NL2DataError):
    """Raised when tenant scope cannot be established safely."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.GOVERNANCE,
            ErrorCode.TENANT_CONTEXT_REJECTED,
            message,
            retryable=False,
            details=details,
        )


class TenantContextValidationResult(BaseModel):
    """Outcome of trusted-context validation; invalid means denied."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    reasons: tuple[str, ...] = Field(default_factory=tuple, max_length=32)

    @property
    def allowed(self) -> bool:
        return self.valid


def validate_tenant_scope(
    scope: TenantScopeContext | None,
    *,
    client_tenant_hint: str | None = None,
) -> TenantContextValidationResult:
    """Validate the trusted context and the optional untrusted routing hint.

    The client hint is recorded only as untrusted routing metadata: a hint
    without a trusted context cannot establish authority, and a hint that
    conflicts with the trusted context is denied.
    """
    if scope is None:
        if client_tenant_hint:
            return TenantContextValidationResult(
                valid=False,
                reasons=(
                    "client tenant hint cannot establish authority without a trusted context",
                ),
            )
        return TenantContextValidationResult(
            valid=False,
            reasons=("missing trusted tenant context",),
        )

    reasons: list[str] = []
    if not scope.tenant.lifecycle_state.active:
        reasons.append(
            f"tenant lifecycle state '{scope.tenant.lifecycle_state.value}' is not active"
        )
    capabilities = ISOLATION_PROFILES.get(scope.tenant.isolation_profile)
    if capabilities is None:
        reasons.append(
            f"isolation profile '{scope.tenant.isolation_profile.value}' is unsupported"
        )
    elif not capabilities.tenant_scoped_execution_supported:
        reasons.append(
            "isolation profile "
            f"'{scope.tenant.isolation_profile.value}' cannot enforce tenant-scoped execution"
        )
    if scope.tenant.enforcement_fingerprint is None:
        reasons.append("tenant isolation enforcement has not been verified by the host")
    if client_tenant_hint is not None and client_tenant_hint != scope.tenant.tenant_id:
        reasons.append("client tenant hint conflicts with the trusted tenant context")
    return TenantContextValidationResult(valid=not reasons, reasons=tuple(reasons))
