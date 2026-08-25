"""Durable lease ownership and fencing capability contracts.

A shared state store may additionally implement :class:`WorkflowLeaseStore`
to coordinate execution ownership across workers: one workflow has at most
one active lease per tenant/workflow key, and every protected mutation
requires the current owner and a monotonic fencing token.  SQLite and
in-memory stores intentionally do not implement these capabilities - file
locking and process-local state already bound them to one worker.

Worker identity is an opaque bounded process/instance reference supplied by
the host; it is never an authorization identity, never a tenant claim, and
never exposed through public outcomes.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"


class WorkflowLease(BaseModel):
    """One durable execution lease for a tenant-scoped workflow.

    ``fencing_token`` increases monotonically on every takeover, so a
    worker that lost ownership can never satisfy a protected mutation with
    its superseded token.  Only the opaque owner reference and expiry are
    persisted - never raw tenant or principal claims.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    tenant_scope_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    owner_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    fencing_token: int = Field(ge=1, le=2**63 - 1)
    expires_at: datetime
    updated_at: datetime

    def valid(self, *, now: datetime, clock_tolerance_seconds: float = 0.0) -> bool:
        """Whether the lease is still valid under conservative server time."""
        return self.expires_at > now + timedelta(seconds=clock_tolerance_seconds)


@runtime_checkable
class WorkflowLeaseStore(Protocol):
    """Optional durable capability: lease ownership for shared execution.

    Acquisition is atomic and allows takeover only after the previous
    lease is expired (with conservative clock tolerance); renewal, release,
    and inspection are keyed by tenant scope and workflow identity and
    require the current owner/token for any mutation.
    """

    def acquire_lease(
        self,
        workflow_id: str,
        *,
        owner_id: str,
        tenant_scope_fingerprint: str | None = None,
        ttl_seconds: float | None = None,
    ) -> WorkflowLease:
        """Atomically claim the workflow lease; a valid lease is busy."""
        ...

    def renew_lease(
        self,
        workflow_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        tenant_scope_fingerprint: str | None = None,
    ) -> WorkflowLease:
        """Extend the lease only for its current owner and fencing token."""
        ...

    def release_lease(
        self,
        workflow_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        tenant_scope_fingerprint: str | None = None,
    ) -> bool:
        """Release the lease for its current owner/token; ``False`` otherwise."""
        ...

    def inspect_lease(
        self,
        workflow_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> WorkflowLease | None:
        """Return the stored lease within the matching scope or ``None``."""
        ...


@runtime_checkable
class FencedStateStore(WorkflowLeaseStore, Protocol):
    """Optional durable capability: ownership-aware state mutations.

    A fenced store implements the lease capability *and* accepts the current
    lease owner and fencing token on its ``update`` and
    ``complete_idempotency`` calls, rejecting the mutation when the stored
    lease no longer matches - so a stale worker can never commit after
    takeover.  Stores without the capability (SQLite, in-memory) lack the
    lease methods entirely and simply never receive ownership arguments.
    """

    def update(
        self,
        workflow_id: str,
        expected_status: object,
        state: object,
        *,
        expected_version: int | None = None,
        tenant_scope_fingerprint: str | None = None,
        owner_id: str | None = None,
        fencing_token: int | None = None,
    ) -> None:
        """Compare-and-set state; when ownership is supplied it must match."""
        ...

    def complete_idempotency(
        self,
        key: str,
        *,
        workflow_id: str,
        terminal_outcome_fingerprint: str,
        tenant_scope_fingerprint: str | None = None,
        owner_id: str | None = None,
        fencing_token: int | None = None,
    ) -> object:
        """Store the terminal outcome reference; ownership must match."""
        ...


def validate_lease_identity(
    workflow_id: str, *, owner_id: str | None = None, scope: str | None = None
) -> None:
    """Reject lease identities that are not identifier/fingerprint-safe.

    Raises ``ValueError`` so hosts fail fast before any backend statement
    is constructed with unsafe identity material.
    """
    if re.fullmatch(_IDENTIFIER_PATTERN, workflow_id) is None:
        raise ValueError("lease workflow identity is not identifier-safe")
    if owner_id is not None and re.fullmatch(_IDENTIFIER_PATTERN, owner_id) is None:
        raise ValueError("lease owner identity is not identifier-safe")
    if scope is not None and re.fullmatch(_FINGERPRINT_PATTERN, scope) is None:
        raise ValueError("lease scope must be a sha256 fingerprint")
