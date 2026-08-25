"""Replaceable Semantic Model Bundle catalog protocol and reference catalog.

A catalog owns the immutable publication lifecycle: publish validates a
bundle before it becomes available, lookup retrieves published versions,
activation is an atomic pointer change to a complete validated snapshot,
and rollback selects a previously active version without ever mutating or
deleting a published artifact.

The protocol is provider-neutral and synchronous; the reference
implementation is process-local and bounded.  A later shared/service
catalog can implement the same protocol without changing View or IR
callers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nl2data_core.metadata.policy import (
    ActivationCheckResult,
    ProductionActivationContext,
)

from .models import (
    BUNDLE_SCHEMA_VERSION,
    SemanticModelBundle,
)
from .validation import BundleValidationResult, validate_bundle

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"

#: Bounded number of issues reported by one catalog operation.
_MAX_ISSUES = 16


def _utc_now() -> datetime:
    return datetime.now(UTC)


class BundleCatalogIssue(BaseModel):
    """One structured catalog issue with a safe reason code."""

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


class BundleCatalogOutcome(BaseModel):
    """Immutable result of one catalog operation.

    Success kinds (``published``, ``activated``, ``rolled_back``) carry the
    bundle they concern; failure kinds (``conflict``, ``not_found``,
    ``rejected``, ``no_history``) carry structured issues and never a
    partial bundle.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[
        "published", "activated", "rolled_back", "conflict", "not_found", "rejected",
        "no_history",
    ]
    bundle: SemanticModelBundle | None = None
    issues: tuple[BundleCatalogIssue, ...] = Field(
        default_factory=tuple, max_length=_MAX_ISSUES
    )

    @model_validator(mode="after")
    def _consistent(self) -> BundleCatalogOutcome:
        if self.kind in {"published", "activated", "rolled_back"}:
            if self.bundle is None:
                raise ValueError("successful catalog outcomes must carry a bundle")
            if self.issues:
                raise ValueError("successful catalog outcomes must not carry issues")
        else:
            if self.bundle is not None:
                raise ValueError("failed catalog outcomes must not carry a bundle")
            if not self.issues:
                raise ValueError("failed catalog outcomes must carry at least one issue")
        return self

    @property
    def success(self) -> bool:
        """Whether the catalog operation succeeded."""
        return self.kind in {"published", "activated", "rolled_back"}

    def issue_codes(self) -> list[str]:
        """The bounded issue codes of this outcome."""
        return [issue.code for issue in self.issues]

    def safe_payload(self) -> dict[str, object]:
        """Serialize with safe codes and bundle fingerprints only."""
        return {
            "kind": self.kind,
            "bundle": (
                {"bundle_id": self.bundle.bundle_id, "fingerprint": self.bundle.fingerprint}
                if self.bundle is not None
                else None
            ),
            "issues": [issue.safe_payload() for issue in self.issues],
        }


def _success(
    kind: Literal["published", "activated", "rolled_back"], bundle: SemanticModelBundle
) -> BundleCatalogOutcome:
    return BundleCatalogOutcome(kind=kind, bundle=bundle)


def _failure(
    kind: Literal["conflict", "not_found", "rejected", "no_history"],
    code: str,
    message: str,
    *,
    member_id: str | None = None,
) -> BundleCatalogOutcome:
    return BundleCatalogOutcome(
        kind=kind,
        issues=(BundleCatalogIssue(code=code, message=message, member_id=member_id),),
    )


class BundlePublication(BaseModel):
    """An immutable publication record; never mutated after creation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bundle: SemanticModelBundle
    published_at: datetime = Field(default_factory=_utc_now)


class SemanticBundleCatalog(Protocol):
    """Replaceable catalog protocol for the bundle lifecycle.

    Implementations SHALL publish only validated bundles, expose complete
    immutable snapshots, change the active pointer atomically, and roll
    back only to a previously published valid bundle without mutating any
    published artifact.  When a ``production`` activation context is
    supplied, publish/activate/rollback SHALL additionally require the
    bundle to be bound to the context's active discovery snapshot and, for
    activation, SHALL pass the full production activation check before the
    pointer changes.
    """

    def publish(
        self,
        bundle: SemanticModelBundle,
        *,
        production: ProductionActivationContext | None = None,
    ) -> BundleCatalogOutcome: ...

    def get(self, bundle_id: str, version: str) -> SemanticModelBundle | None: ...

    def versions(self, bundle_id: str) -> tuple[SemanticModelBundle, ...]: ...

    def active(self, bundle_id: str) -> SemanticModelBundle | None: ...

    def activate(
        self,
        bundle_id: str,
        version: str,
        *,
        production: ProductionActivationContext | None = None,
    ) -> BundleCatalogOutcome: ...

    def rollback(
        self,
        bundle_id: str,
        *,
        production: ProductionActivationContext | None = None,
    ) -> BundleCatalogOutcome: ...


class InMemorySemanticBundleCatalog:
    """A bounded process-local catalog with atomic activation.

    Publications are immutable records; the active bundle is a single
    pointer per bundle id, swapped only to a previously published valid
    snapshot.  Activation revalidates the bundle and requires every
    declared dependency to be published with a matching fingerprint, so
    stale or incompatible bundles fail closed before any View can resolve
    against them.
    """

    def __init__(
        self,
        *,
        supported_schema_versions: tuple[int, ...] = (BUNDLE_SCHEMA_VERSION,),
    ) -> None:
        self._supported_schema_versions = supported_schema_versions
        self._publications: dict[str, tuple[BundlePublication, ...]] = {}
        self._active: dict[str, BundlePublication] = {}
        self._history: dict[str, tuple[BundlePublication, ...]] = {}

    def publish(
        self,
        bundle: SemanticModelBundle,
        *,
        production: ProductionActivationContext | None = None,
    ) -> BundleCatalogOutcome:
        """Validate and publish one immutable bundle version.

        When a production activation context is supplied, the bundle must
        be bound to the context's active discovery snapshot; bundles built
        from an older or unknown snapshot are rejected before publication.
        """
        result = validate_bundle(
            bundle,
            supported_schema_versions=self._supported_schema_versions,
            expected_snapshot_fingerprint=_expected_snapshot_fingerprint(production),
        )
        if not result.valid:
            return _failure_from_validation(result)
        existing = self._publications.get(bundle.bundle_id, ())
        if any(
            publication.bundle.model_version == bundle.model_version
            for publication in existing
        ):
            return _failure(
                "conflict",
                "version_exists",
                f"bundle '{bundle.bundle_id}' version '{bundle.model_version}' "
                "is already published",
            )
        record = BundlePublication(bundle=bundle)
        self._publications[bundle.bundle_id] = existing + (record,)
        return _success("published", bundle)

    def get(self, bundle_id: str, version: str) -> SemanticModelBundle | None:
        """The published bundle with the given id and version, or ``None``."""
        for publication in self._publications.get(bundle_id, ()):
            if publication.bundle.model_version == version:
                return publication.bundle
        return None

    def versions(self, bundle_id: str) -> tuple[SemanticModelBundle, ...]:
        """Every published version of a bundle as an immutable snapshot."""
        return tuple(
            publication.bundle
            for publication in self._publications.get(bundle_id, ())
        )

    def active(self, bundle_id: str) -> SemanticModelBundle | None:
        """The active validated snapshot, or ``None`` when not activated."""
        publication = self._active.get(bundle_id)
        return publication.bundle if publication is not None else None

    def activate(
        self,
        bundle_id: str,
        version: str,
        *,
        production: ProductionActivationContext | None = None,
    ) -> BundleCatalogOutcome:
        """Atomically point the active pointer at a published valid bundle.

        When a production activation context is supplied, activation also
        requires the bundle to be bound to the context's active snapshot and
        the full production activation check (drift severity, freshness,
        completeness, tenant scope, catalog compatibility) to pass.  Any
        rejection preserves the current active pointer unchanged.
        """
        bundle = self.get(bundle_id, version)
        if bundle is None:
            return _failure(
                "not_found",
                "bundle_not_found",
                f"no published bundle '{bundle_id}' version '{version}' exists",
            )
        result = validate_bundle(
            bundle,
            supported_schema_versions=self._supported_schema_versions,
            expected_snapshot_fingerprint=_expected_snapshot_fingerprint(production),
        )
        if not result.valid:
            return _failure_from_validation(result)
        if production is not None:
            check = production.check()
            if not check.allowed:
                return _failure_from_activation_check(check)
        for dependency in bundle.dependencies:
            dependency_bundle = self.get(dependency.bundle_id, dependency.version)
            if (
                dependency_bundle is None
                or dependency_bundle.fingerprint != dependency.fingerprint
            ):
                return _failure(
                    "rejected",
                    "dependency_unavailable",
                    f"dependency '{dependency.dependency_id}' is unavailable or "
                    "has a different fingerprint",
                    member_id=dependency.dependency_id,
                )
        record = BundlePublication(bundle=bundle)
        previous = self._active.get(bundle_id)
        if previous is not None:
            self._history[bundle_id] = (previous,) + self._history.get(bundle_id, ())
        self._active[bundle_id] = record
        return _success("activated", bundle)

    def rollback(
        self,
        bundle_id: str,
        *,
        production: ProductionActivationContext | None = None,
    ) -> BundleCatalogOutcome:
        """Move the active pointer to the previous active version.

        Published artifacts are never mutated or deleted; only the pointer
        changes, and rollback is possible only while a prior active version
        exists.  When a production activation context is supplied, the
        rollback target must still satisfy the production activation check
        and remain bound to the context's active discovery snapshot.
        """
        if bundle_id not in self._active:
            return _failure(
                "not_found",
                "bundle_not_active",
                f"bundle '{bundle_id}' has no active version",
            )
        history = self._history.get(bundle_id, ())
        if not history:
            return _failure(
                "no_history",
                "no_rollback_history",
                f"bundle '{bundle_id}' has no previously active version",
            )
        previous, *rest = history
        if production is not None:
            check = production.check()
            if not check.allowed:
                return _failure_from_activation_check(check)
            result = validate_bundle(
                previous.bundle,
                supported_schema_versions=self._supported_schema_versions,
                expected_snapshot_fingerprint=_expected_snapshot_fingerprint(production),
            )
            if not result.valid:
                return _failure_from_validation(result)
        self._active[bundle_id] = previous
        self._history[bundle_id] = tuple(rest)
        return _success("rolled_back", previous.bundle)


def _expected_snapshot_fingerprint(
    production: ProductionActivationContext | None,
) -> str | None:
    """The active snapshot fingerprint a production context requires, if any."""
    if production is None or production.active_snapshot is None:
        return None
    return production.active_snapshot.fingerprint


def _failure_from_activation_check(
    check: ActivationCheckResult,
) -> BundleCatalogOutcome:
    """Convert an activation check into a rejected catalog outcome.

    The production issue codes (snapshot_unavailable, snapshot_partial,
    snapshot_stale, source_changed, catalog_incompatible, snapshot_unauthorized,
    blocking_drift) are preserved so hosts can map them to review flows.
    """
    issues = tuple(
        BundleCatalogIssue(
            code=issue.code,
            message=issue.message,
            member_id=issue.member_id,
        )
        for issue in check.issues
    )
    return BundleCatalogOutcome(kind="rejected", issues=issues)


def _failure_from_validation(result: BundleValidationResult) -> BundleCatalogOutcome:
    """Convert a validation result into a rejected catalog outcome."""
    issues = tuple(
        BundleCatalogIssue(
            code=issue.code,
            message=issue.message,
            member_id=issue.member_id,
        )
        for issue in result.issues
    )
    return BundleCatalogOutcome(kind="rejected", issues=issues)
