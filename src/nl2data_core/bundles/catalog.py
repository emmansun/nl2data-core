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

from nl2data_core.assembly.manifest import AcceptedAssertionManifest
from nl2data_core.assembly.models import AssemblyDraft
from nl2data_core.assembly.store import AssemblyDraftStore
from nl2data_core.metadata.policy import (
    ActivationCheckResult,
    ProductionActivationContext,
)

from .models import (
    BUNDLE_SCHEMA_VERSION,
    SemanticModelBundle,
)
from .publication import (
    PublishAuditRecord,
    PublishedVersionState,
    PublishIdempotencyStatus,
    SupersessionMetadata,
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
        "published", "reused", "activated", "rolled_back", "conflict", "not_found",
        "rejected", "no_history", "deprecated", "retired",
    ]
    bundle: SemanticModelBundle | None = None
    audit_reference: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    superseded_fingerprint: str | None = None
    idempotency_status: PublishIdempotencyStatus | None = None
    issues: tuple[BundleCatalogIssue, ...] = Field(
        default_factory=tuple, max_length=_MAX_ISSUES
    )

    @model_validator(mode="after")
    def _consistent(self) -> BundleCatalogOutcome:
        if self.kind in {
            "published", "reused", "activated", "rolled_back", "deprecated", "retired"
        }:
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
        return self.kind in {
            "published", "reused", "activated", "rolled_back", "deprecated", "retired"
        }

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
            "audit_reference": self.audit_reference,
            "superseded_fingerprint": self.superseded_fingerprint,
            "idempotency_status": (
                self.idempotency_status.value
                if self.idempotency_status is not None
                else None
            ),
            "issues": [issue.safe_payload() for issue in self.issues],
        }


def _success(
    kind: Literal[
        "published", "reused", "activated", "rolled_back", "deprecated", "retired"
    ],
    bundle: SemanticModelBundle,
    *,
    audit_reference: str | None = None,
    superseded_fingerprint: str | None = None,
    idempotency_status: PublishIdempotencyStatus | None = None,
) -> BundleCatalogOutcome:
    return BundleCatalogOutcome(
        kind=kind,
        bundle=bundle,
        audit_reference=audit_reference,
        superseded_fingerprint=superseded_fingerprint,
        idempotency_status=idempotency_status,
    )


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
    accepted_assertion_manifest: AcceptedAssertionManifest | None = None
    audit: PublishAuditRecord | None = None
    state: PublishedVersionState = PublishedVersionState.AVAILABLE
    supersession: SupersessionMetadata = Field(default_factory=SupersessionMetadata)
    published_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def _manifest_matches_bundle(self) -> BundlePublication:
        manifest = self.accepted_assertion_manifest
        if manifest is not None and (
            manifest.bundle_id != self.bundle.bundle_id
            or manifest.bundle_fingerprint != self.bundle.fingerprint
        ):
            raise ValueError("accepted assertion manifest does not match publication")
        if self.audit is not None and (
            self.audit.bundle_id != self.bundle.bundle_id
            or self.audit.bundle_fingerprint != self.bundle.fingerprint
        ):
            raise ValueError("publish audit does not match publication")
        return self


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
        accepted_assertion_manifest: AcceptedAssertionManifest | None = None,
        audit: PublishAuditRecord | None = None,
        production: ProductionActivationContext | None = None,
        draft: AssemblyDraft | None = None,
        expected_revision: int | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome: ...

    def get(self, bundle_id: str, version: str) -> SemanticModelBundle | None: ...

    def versions(self, bundle_id: str) -> tuple[SemanticModelBundle, ...]: ...

    def accepted_assertion_manifest(
        self,
        bundle_id: str,
        fingerprint: str,
    ) -> AcceptedAssertionManifest | None: ...

    def publish_audit(
        self,
        bundle_id: str,
        fingerprint: str,
    ) -> PublishAuditRecord | None: ...

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
        draft_store: AssemblyDraftStore | None = None,
    ) -> None:
        self._supported_schema_versions = supported_schema_versions
        self._draft_store = draft_store
        self._publications: dict[tuple[str | None, str], tuple[BundlePublication, ...]] = {}
        self._active: dict[tuple[str | None, str], BundlePublication] = {}
        self._history: dict[tuple[str | None, str], tuple[BundlePublication, ...]] = {}

    def publish(
        self,
        bundle: SemanticModelBundle,
        *,
        accepted_assertion_manifest: AcceptedAssertionManifest | None = None,
        audit: PublishAuditRecord | None = None,
        production: ProductionActivationContext | None = None,
        draft: AssemblyDraft | None = None,
        expected_revision: int | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        """Validate and publish one immutable bundle version.

        When a production activation context is supplied, the bundle must
        be bound to the context's active discovery snapshot; bundles built
        from an older or unknown snapshot are rejected before publication.
        """
        if draft is not None:
            if expected_revision is None:
                raise ValueError("draft publication requires expected_revision")
            draft.require_revision(expected_revision)
            if self._draft_store is not None:
                if tenant_scope_fingerprint is None:
                    raise ValueError("draft publication requires tenant scope")
                authoritative = self._draft_store.get(
                    draft.draft_id,
                    tenant_scope_fingerprint=tenant_scope_fingerprint,
                )
                if authoritative is None:
                    return _failure(
                        "not_found",
                        "draft_not_found",
                        "the authoritative assembly draft does not exist",
                    )
                if (
                    authoritative.draft_revision != expected_revision
                    or authoritative.file_payload() != draft.file_payload()
                ):
                    return _failure(
                        "conflict",
                        "draft_revision_conflict",
                        "the authoritative assembly draft changed before publication",
                    )
        result = validate_bundle(
            bundle,
            supported_schema_versions=self._supported_schema_versions,
            expected_snapshot_fingerprint=_expected_snapshot_fingerprint(production),
        )
        if not result.valid:
            return _failure_from_validation(result)
        key = (tenant_scope_fingerprint, bundle.bundle_id)
        existing = self._publications.get(key, ())
        for publication in existing:
            if publication.bundle.fingerprint == bundle.fingerprint:
                return _success(
                    "reused",
                    publication.bundle,
                    audit_reference=(
                        publication.audit.audit_id
                        if publication.audit is not None
                        else None
                    ),
                    superseded_fingerprint=(
                        publication.supersession.predecessor_fingerprint
                    ),
                    idempotency_status=PublishIdempotencyStatus.REUSED,
                )
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
        if accepted_assertion_manifest is not None and (
            accepted_assertion_manifest.bundle_id != bundle.bundle_id
            or accepted_assertion_manifest.bundle_fingerprint != bundle.fingerprint
        ):
            return _failure(
                "rejected",
                "manifest_mismatch",
                "accepted assertion manifest does not match the published bundle",
            )
        if audit is not None and (
            audit.bundle_id != bundle.bundle_id
            or audit.bundle_fingerprint != bundle.fingerprint
        ):
            return _failure(
                "rejected",
                "audit_mismatch",
                "publish audit does not match the published bundle",
            )
        predecessor = existing[-1] if existing else None
        predecessor_fingerprint = (
            predecessor.bundle.fingerprint if predecessor is not None else None
        )
        record = BundlePublication(
            bundle=bundle,
            accepted_assertion_manifest=accepted_assertion_manifest,
            audit=audit,
            supersession=SupersessionMetadata(
                predecessor_fingerprint=predecessor_fingerprint,
            ),
        )
        if predecessor is not None:
            predecessor = predecessor.model_copy(
                update={
                    "supersession": predecessor.supersession.model_copy(
                        update={"successor_fingerprint": bundle.fingerprint}
                    ),
                    "state": (
                        predecessor.state
                        if predecessor.state is PublishedVersionState.ACTIVE
                        else PublishedVersionState.SUPERSEDED
                    ),
                }
            )
            existing = existing[:-1] + (predecessor,)
        self._publications[key] = existing + (record,)
        return _success(
            "published",
            bundle,
            audit_reference=audit.audit_id if audit is not None else None,
            superseded_fingerprint=predecessor_fingerprint,
            idempotency_status=PublishIdempotencyStatus.CREATED,
        )

    def get(
        self,
        bundle_id: str,
        version: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> SemanticModelBundle | None:
        """The published bundle with the given id and version, or ``None``."""
        for publication in self._publications.get((tenant_scope_fingerprint, bundle_id), ()):
            if publication.bundle.model_version == version:
                return publication.bundle
        return None

    def get_by_fingerprint(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> SemanticModelBundle | None:
        """Return one immutable published bundle by semantic fingerprint."""
        publication = self._publication_by_fingerprint(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )
        return publication.bundle if publication is not None else None

    def versions(
        self,
        bundle_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> tuple[SemanticModelBundle, ...]:
        """Every published version of a bundle as an immutable snapshot."""
        return tuple(
            publication.bundle
            for publication in self._publications.get(
                (tenant_scope_fingerprint, bundle_id), ()
            )
        )

    def accepted_assertion_manifest(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> AcceptedAssertionManifest | None:
        """Return the immutable manifest linked to a published fingerprint."""
        for publication in self._publications.get(
            (tenant_scope_fingerprint, bundle_id), ()
        ):
            if publication.bundle.fingerprint == fingerprint:
                return publication.accepted_assertion_manifest
        return None

    def publish_audit(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> PublishAuditRecord | None:
        """Return the immutable audit record linked to a publication."""
        for publication in self._publications.get(
            (tenant_scope_fingerprint, bundle_id), ()
        ):
            if publication.bundle.fingerprint == fingerprint:
                return publication.audit
        return None

    def publication_records(
        self,
        bundle_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> tuple[BundlePublication, ...]:
        """Return immutable publication metadata in supersession order."""
        return self._publications.get((tenant_scope_fingerprint, bundle_id), ())

    def supersession_chain(
        self,
        bundle_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> tuple[BundlePublication, ...]:
        """Return publications in immutable predecessor-to-successor order."""
        return self._publications.get((tenant_scope_fingerprint, bundle_id), ())

    def active(
        self,
        bundle_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> SemanticModelBundle | None:
        """The active validated snapshot, or ``None`` when not activated."""
        publication = self._active.get((tenant_scope_fingerprint, bundle_id))
        return publication.bundle if publication is not None else None

    def activate(
        self,
        bundle_id: str,
        version: str,
        *,
        production: ProductionActivationContext | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        """Atomically point the active pointer at a published valid bundle.

        When a production activation context is supplied, activation also
        requires the bundle to be bound to the context's active snapshot and
        the full production activation check (drift severity, freshness,
        completeness, tenant scope, catalog compatibility) to pass.  Any
        rejection preserves the current active pointer unchanged.
        """
        publication = next(
            (
                item
                for item in self._publications.get(
                    (tenant_scope_fingerprint, bundle_id), ()
                )
                if item.bundle.model_version == version
            ),
            None,
        )
        if publication is None:
            return _failure(
                "not_found",
                "bundle_not_found",
                f"no published bundle '{bundle_id}' version '{version}' exists",
            )
        return self._activate_publication(
            bundle_id,
            publication,
            production=production,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )

    def activate_fingerprint(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        production: ProductionActivationContext | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        """Atomically activate one immutable publication by fingerprint."""
        publication = self._publication_by_fingerprint(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )
        if publication is None:
            return _failure(
                "not_found",
                "bundle_not_found",
                f"no published bundle '{bundle_id}' fingerprint '{fingerprint}' exists",
            )
        return self._activate_publication(
            bundle_id,
            publication,
            production=production,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )

    def _activate_publication(
        self,
        bundle_id: str,
        publication: BundlePublication,
        *,
        production: ProductionActivationContext | None,
        tenant_scope_fingerprint: str | None,
    ) -> BundleCatalogOutcome:
        bundle = publication.bundle
        if publication.state is PublishedVersionState.RETIRED:
            return _failure(
                "rejected",
                "bundle_retired",
                "retired bundle versions cannot be activated",
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
            dependency_bundle = self.get(
                dependency.bundle_id,
                dependency.version,
                tenant_scope_fingerprint=tenant_scope_fingerprint,
            )
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
        key = (tenant_scope_fingerprint, bundle_id)
        previous = self._active.get(key)
        if previous is not None:
            self._history[key] = (previous,) + self._history.get(key, ())
            self._replace_publication(
                bundle_id,
                previous.model_copy(update={"state": PublishedVersionState.SUPERSEDED}),
                tenant_scope_fingerprint=tenant_scope_fingerprint,
            )
        active = publication.model_copy(update={"state": PublishedVersionState.ACTIVE})
        self._replace_publication(
            bundle_id,
            active,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )
        self._active[key] = active
        return _success("activated", bundle)

    def rollback(
        self,
        bundle_id: str,
        *,
        production: ProductionActivationContext | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        """Move the active pointer to the previous active version.

        Published artifacts are never mutated or deleted; only the pointer
        changes, and rollback is possible only while a prior active version
        exists.  When a production activation context is supplied, the
        rollback target must still satisfy the production activation check
        and remain bound to the context's active discovery snapshot.
        """
        key = (tenant_scope_fingerprint, bundle_id)
        if key not in self._active:
            return _failure(
                "not_found",
                "bundle_not_active",
                f"bundle '{bundle_id}' has no active version",
            )
        history = self._history.get(key, ())
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
        current = self._active[key]
        current = current.model_copy(update={"state": PublishedVersionState.SUPERSEDED})
        previous = previous.model_copy(update={"state": PublishedVersionState.ACTIVE})
        self._replace_publication(
            bundle_id,
            current,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )
        self._replace_publication(
            bundle_id,
            previous,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )
        self._active[key] = previous
        self._history[key] = tuple(rest)
        return _success("rolled_back", previous.bundle)

    def rollback_to_fingerprint(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        production: ProductionActivationContext | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        """Change only the active pointer to a prior immutable fingerprint."""
        target = self._publication_by_fingerprint(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )
        if target is None:
            return _failure(
                "not_found",
                "bundle_not_found",
                f"no published bundle '{bundle_id}' fingerprint '{fingerprint}' exists",
            )
        current = self._active.get((tenant_scope_fingerprint, bundle_id))
        if current is None:
            return _failure(
                "not_found",
                "bundle_not_active",
                f"bundle '{bundle_id}' has no active version",
            )
        if target.bundle.fingerprint == current.bundle.fingerprint:
            return _success("rolled_back", target.bundle)
        outcome = self._activate_publication(
            bundle_id,
            target,
            production=production,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )
        if outcome.success:
            return BundleCatalogOutcome(kind="rolled_back", bundle=target.bundle)
        return outcome

    def set_version_state(
        self,
        bundle_id: str,
        fingerprint: str,
        state: Literal[PublishedVersionState.DEPRECATED, PublishedVersionState.RETIRED],
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        """Set operator-managed deprecation or retirement metadata."""
        publication = self._publication_by_fingerprint(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )
        if publication is None:
            return _failure(
                "not_found",
                "bundle_not_found",
                f"no published bundle '{bundle_id}' fingerprint '{fingerprint}' exists",
            )
        if (
            state is PublishedVersionState.RETIRED
            and self._active.get((tenant_scope_fingerprint, bundle_id)) is not None
            and self._active[(tenant_scope_fingerprint, bundle_id)].bundle.fingerprint
            == fingerprint
        ):
            return _failure(
                "rejected",
                "active_bundle_retirement",
                "the active bundle cannot be retired",
            )
        updated = publication.model_copy(update={"state": state})
        self._replace_publication(
            bundle_id,
            updated,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )
        outcome_kind: Literal["deprecated", "retired"] = (
            "deprecated"
            if state is PublishedVersionState.DEPRECATED
            else "retired"
        )
        return _success(outcome_kind, updated.bundle)

    def _publication_by_fingerprint(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundlePublication | None:
        for publication in self._publications.get(
            (tenant_scope_fingerprint, bundle_id), ()
        ):
            if publication.bundle.fingerprint == fingerprint:
                return publication
        return None

    def _replace_publication(
        self,
        bundle_id: str,
        replacement: BundlePublication,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> None:
        key = (tenant_scope_fingerprint, bundle_id)
        self._publications[key] = tuple(
            replacement
            if publication.bundle.fingerprint == replacement.bundle.fingerprint
            else publication
            for publication in self._publications.get(key, ())
        )


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
