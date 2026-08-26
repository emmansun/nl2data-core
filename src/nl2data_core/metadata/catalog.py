"""Durable semantic catalog boundary: snapshots, proposal sets, and Bundles.

The reference :class:`~nl2data_core.metadata.production.SnapshotLedger` and
:class:`~nl2data_core.bundles.catalog.InMemorySemanticBundleCatalog` are
process-local host implementations.  :class:`SemanticSnapshotCatalog` is the
shared boundary a durable catalog (for example the optional PostgreSQL
catalog package) implements so hosts can persist and coordinate the whole
metadata-to-Bundle lifecycle across restarts and workers without changing
View, IR, or engine callers.

Implementations SHALL:

- persist only bounded canonical safe representations and revalidate
  fingerprints, schema versions, tenant/source scope, and compatibility on
  every read and activation;
- scope every scoped record by an opaque tenant scope fingerprint (never a
  raw tenant identifier) and fail closed across scopes;
- publish only validated immutable Bundles, activate atomically one complete
  compatible version per catalog scope, and roll back only to a previously
  published valid version;
- provide bounded cleanup that preserves active snapshots, active Bundles,
  and required dependencies;
- normalize backend failures without leaking DSNs or backend exception text.

The boundary is provider-neutral: it imports only core models and protocols,
never database drivers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from nl2data_core.bundles.catalog import BundleCatalogOutcome
from nl2data_core.bundles.models import SemanticModelBundle

from .drift import DriftDecision, DriftOverride
from .models import MetadataSnapshot
from .policy import SnapshotActivationPolicy
from .production import LedgerActivation, SnapshotLifecycleRecord
from .proposals import SemanticProposalSet

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"

#: Bounded number of rejected items reported by one reload.
_MAX_REJECTED = 16


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CatalogReloadIssue(BaseModel):
    """One bounded revalidation issue with a safe reason code."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=256)
    member_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)

    def safe_payload(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "message": self.message,
            "member_id": self.member_id,
        }


class CatalogReloadReport(BaseModel):
    """Immutable result of one startup/reload revalidation pass.

    Counts record how many active pointers were revalidated; ``rejected``
    carries the bounded issues for pointers whose artifact failed
    revalidation (fingerprint, scope, schema, or compatibility mismatch).
    A rejected active pointer is never exposed for query-time resolution.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    checked_at: datetime = Field(default_factory=_utc_now)
    active_snapshots_revalidated: int = Field(ge=0, le=1_048_576)
    active_bundles_revalidated: int = Field(ge=0, le=1_048_576)
    rejected: tuple[CatalogReloadIssue, ...] = Field(
        default_factory=tuple, max_length=_MAX_REJECTED
    )

    @property
    def ok(self) -> bool:
        """Whether every active pointer revalidated successfully."""
        return not self.rejected

    def safe_payload(self) -> dict[str, object]:
        """Serialize with counts and safe issue codes only."""
        return {
            "checked_at": self.checked_at.isoformat(),
            "active_snapshots_revalidated": self.active_snapshots_revalidated,
            "active_bundles_revalidated": self.active_bundles_revalidated,
            "rejected": [issue.safe_payload() for issue in self.rejected],
        }


class SemanticSnapshotCatalog(Protocol):
    """Replaceable durable catalog boundary for the metadata lifecycle.

    Snapshots and proposal sets are scoped by an opaque tenant scope
    fingerprint; Bundle operations accept an optional scope (``None`` uses
    the local non-tenant namespace) so both tenant-scoped and unscoped
    deployments share one implementation.
    """

    # -- snapshots ------------------------------------------------------

    def register_snapshot(
        self,
        snapshot: MetadataSnapshot,
        *,
        tenant_scope_fingerprint: str,
        retained_for_seconds: float | None = None,
    ) -> SnapshotLifecycleRecord:
        """Retain one snapshot as evidence (never activates by default)."""
        ...

    def snapshot(
        self,
        snapshot_fingerprint: str,
        *,
        tenant_scope_fingerprint: str,
    ) -> MetadataSnapshot | None:
        """The registered snapshot with the given fingerprint, or ``None``.

        The read revalidates the fingerprint and tenant scope; a mismatch
        fails closed.
        """
        ...

    def activate_snapshot(
        self,
        snapshot_fingerprint: str,
        *,
        tenant_scope_fingerprint: str,
        policy: SnapshotActivationPolicy | None = None,
        drift_decision: DriftDecision | None = None,
        overrides: tuple[DriftOverride, ...] = (),
        now: datetime | None = None,
    ) -> LedgerActivation:
        """Atomically activate a registered snapshot under production rules.

        Only registered, structurally complete snapshots activate; the
        active pointer changes only when every activation check passes, and
        a rejected activation leaves the previous active pointer unchanged.
        """
        ...

    def active_snapshot(
        self, source_id: str, tenant_scope_fingerprint: str
    ) -> MetadataSnapshot | None:
        """The active snapshot for one source/tenant scope, or ``None``."""
        ...

    # -- proposal sets --------------------------------------------------

    def save_proposal_set(
        self,
        proposal_set: SemanticProposalSet,
        *,
        tenant_scope_fingerprint: str,
    ) -> None:
        """Persist the latest reviewed proposal set for its snapshot.

        Review operations produce new sets; saving replaces the stored set
        for the bound snapshot fingerprint.  The proposal set must be bound
        to a snapshot registered in the same tenant scope.
        """
        ...

    def proposal_set(
        self,
        snapshot_fingerprint: str,
        *,
        tenant_scope_fingerprint: str,
    ) -> SemanticProposalSet | None:
        """The persisted proposal set for one snapshot, or ``None``."""
        ...

    # -- bundles --------------------------------------------------------

    def publish(
        self,
        bundle: SemanticModelBundle,
        *,
        production: object | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        """Validate and publish one immutable Bundle version."""
        ...

    def get(
        self,
        bundle_id: str,
        version: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> SemanticModelBundle | None:
        """The published Bundle with the given id and version, or ``None``."""
        ...

    def versions(
        self,
        bundle_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> tuple[SemanticModelBundle, ...]:
        """Every published version of a Bundle as an immutable snapshot."""
        ...

    def active(
        self,
        bundle_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> SemanticModelBundle | None:
        """The active validated Bundle, or ``None`` when not activated."""
        ...

    def activate(
        self,
        bundle_id: str,
        version: str,
        *,
        production: object | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        """Atomically point the active pointer at a published valid Bundle."""
        ...

    def rollback(
        self,
        bundle_id: str,
        *,
        production: object | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        """Move the active pointer to the previous active version."""
        ...

    # -- maintenance ----------------------------------------------------

    def cleanup(self, *, now: datetime | None = None) -> int:
        """Remove expired inactive records; preserves active content."""
        ...

    def reload_active(self, *, now: datetime | None = None) -> CatalogReloadReport:
        """Revalidate every active snapshot/Bundle pointer after startup.

        A newer persisted schema or envelope version fails closed; active
        pointers whose artifact no longer revalidates are reported as
        rejected and are not exposed for query-time resolution.
        """
        ...
