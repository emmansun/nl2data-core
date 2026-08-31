"""Host-supplied authorization contracts for semantic lifecycle actions."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nl2data_core.views.models import validate_safe_description

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_MAX_REFERENCE_CHARS = 256


class LifecycleRole(StrEnum):
    """Distinct trusted roles used by lifecycle mutations."""

    AUTHOR = "author"
    REVIEWER = "reviewer"
    APPROVER = "approver"
    PUBLISHER = "publisher"


class LifecycleAction(StrEnum):
    """Lifecycle actions presented to a host authorization hook."""

    CREATE_DRAFT = "create_draft"
    EDIT_DRAFT = "edit_draft"
    SUBMIT_FOR_REVIEW = "submit_for_review"
    REVIEW_ASSERTION = "review_assertion"
    EDIT_ASSERTION = "edit_assertion"
    APPROVE_DRAFT = "approve_draft"
    PUBLISH = "publish"
    ACTIVATE = "activate"
    ROLLBACK = "rollback"


class LifecycleAuthorizationContext(BaseModel):
    """Trusted host identity, scope, and granted lifecycle roles."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operator_reference: str = Field(min_length=1, max_length=_MAX_REFERENCE_CHARS)
    tenant_scope_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    roles: frozenset[LifecycleRole] = Field(max_length=4)

    @field_validator("operator_reference")
    @classmethod
    def _safe_operator(cls, value: str) -> str:
        return validate_safe_description(value)


class LifecycleAuthorizationRequest(BaseModel):
    """Bounded request passed to the host authorization hook."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    context: LifecycleAuthorizationContext
    required_role: LifecycleRole
    action: LifecycleAction
    resource_id: str = Field(pattern=_IDENTIFIER_PATTERN)


class LifecycleAuthorizationDecision(BaseModel):
    """Bounded host decision without backend exception or policy details."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    reason_code: str = Field(default="authorized", pattern=r"^[a-z][a-z0-9_]{0,63}$")


class LifecycleAuthorizer(Protocol):
    """Host authorization hook invoked for every lifecycle mutation."""

    def authorize(
        self,
        request: LifecycleAuthorizationRequest,
    ) -> LifecycleAuthorizationDecision: ...


class LifecycleAuthorizationError(PermissionError):
    """Safe fail-closed lifecycle authorization rejection."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"lifecycle action denied: {reason_code}")


def require_lifecycle_authorization(
    *,
    context: LifecycleAuthorizationContext,
    authorizer: LifecycleAuthorizer,
    required_role: LifecycleRole,
    action: LifecycleAction,
    resource_id: str,
) -> None:
    """Require both a trusted role grant and an affirmative host decision."""
    if required_role not in context.roles:
        raise LifecycleAuthorizationError("missing_lifecycle_role")
    decision = authorizer.authorize(
        LifecycleAuthorizationRequest(
            context=context,
            required_role=required_role,
            action=action,
            resource_id=resource_id,
        )
    )
    if not decision.allowed:
        raise LifecycleAuthorizationError(decision.reason_code)