"""Host-supplied authentication and authorization context."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol

from nl2data_core.assembly import LifecycleRole
from pydantic import BaseModel, ConfigDict, Field


class Permission(StrEnum):
    """Admin service permissions a host may grant to an operator."""

    DISCOVERY_READ = "discovery:read"
    DISCOVERY_RUN = "discovery:run"
    SNAPSHOT_READ = "snapshot:read"
    PROPOSAL_READ = "proposal:read"
    PROPOSAL_REVIEW = "proposal:review"
    BUNDLE_READ = "bundle:read"
    BUNDLE_VALIDATE = "bundle:validate"
    BUNDLE_PUBLISH = "bundle:publish"
    BUNDLE_ACTIVATE = "bundle:activate"
    BUNDLE_ROLLBACK = "bundle:rollback"
    ASSEMBLY_READ = "assembly:read"
    ASSEMBLY_WRITE = "assembly:write"
    ASSEMBLY_REVIEW = "assembly:review"
    ASSEMBLY_APPROVE = "assembly:approve"
    ASSEMBLY_AUDIT = "assembly:audit"
    DRIFT_READ = "drift:read"
    JOB_READ = "job:read"
    JOB_CANCEL = "job:cancel"


class AuthContext(BaseModel):
    """Trusted operator authorization context supplied by the host.

    The admin service never validates tokens or identities itself; it relies on
    the host to provide a trusted context with resolved tenant/source scope
    and permissions.  Client-provided scope values are routing input only and
    must match an authorized scope before any read or mutation proceeds.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    operator_id: str = Field(min_length=1, max_length=256)
    tenant_scope_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_ids: frozenset[str] = Field(default_factory=frozenset, max_length=1_024)
    permissions: frozenset[Permission] = Field(default_factory=frozenset)
    lifecycle_roles: frozenset[LifecycleRole] = Field(default_factory=frozenset, max_length=4)
    audit_reference: str = Field(default="", max_length=512)
    authenticated_at: datetime | None = None

    def is_allowed(self, permission: Permission) -> bool:
        """Whether the context carries the requested permission."""
        return permission in self.permissions

    def is_source_allowed(self, source_id: str) -> bool:
        """Whether the given source is in the authorized source set.

        An empty source set is treated as ``all sources within the tenant
        scope`` for hosts that centralize source authorization elsewhere.
        """
        return not self.source_ids or source_id in self.source_ids


class AuthContextProvider(Protocol):
    """Host-implemented callable that returns a trusted :class:`AuthContext`."""

    def __call__(self) -> AuthContext | None:
        """Return a trusted context, or ``None`` when authentication is absent."""
        ...


class AuthorizationError(Exception):
    """Raised when a request lacks required authentication or authorization."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AuthenticationError(Exception):
    """Raised when a trusted authentication context is missing."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
