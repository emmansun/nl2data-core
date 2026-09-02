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

from collections.abc import Sequence
from datetime import UTC, datetime
from threading import RLock
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nl2data_core.assembly.audit_evidence import (
    MAX_TRAIL_ENTRIES,
    AssemblyAuditEvidenceEntry,
    AuditEventKind,
    AuditTrail,
    PublicationAuditEvidence,
    activation_audit_entry,
    bounded_audit_trail,
    publication_audit_entry,
    rollback_audit_entry,
)
from nl2data_core.assembly.manifest import AcceptedAssertionManifest
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.control_plane.publication.contracts import (
    FrozenReleaseBinding,
    PublicationAggregate,
    PublicationDraftBinding,
    PublicationIntegrityError,
    PublicationRecordSet,
    build_publication_records,
    validate_publication_integrity,
)
from nl2data_core.metadata.policy import (
    ActivationCheckResult,
    ProductionActivationContext,
)
from nl2data_core.verification.models import VerificationStatus, VerificationSuiteEvidence
from nl2data_core.verification.policy import PRODUCTION_POLICY

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

#: Bounded number of audit-evidence entries retained per catalog instance.
#: Retention always protects entries required by active publications,
#: supersession chains, and rollback targets.
_AUDIT_STORE_LIMIT = 4096


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
    verification_evidence_reference: str | None = Field(
        default=None, pattern=_IDENTIFIER_PATTERN
    )
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
            "verification_evidence_reference": self.verification_evidence_reference,
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
    verification_evidence_reference: str | None = None,
    superseded_fingerprint: str | None = None,
    idempotency_status: PublishIdempotencyStatus | None = None,
) -> BundleCatalogOutcome:
    return BundleCatalogOutcome(
        kind=kind,
        bundle=bundle,
        audit_reference=audit_reference,
        verification_evidence_reference=verification_evidence_reference,
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
    verification_evidence: VerificationSuiteEvidence | None = None
    frozen_release_binding: FrozenReleaseBinding | None = None
    audit_evidence: PublicationAuditEvidence | None = None
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
        evidence = self.verification_evidence
        if evidence is not None and (
            evidence.bundle_fingerprint != self.bundle.fingerprint
            or evidence.status is not VerificationStatus.PASSED
        ):
            raise ValueError("verification evidence does not match publication")
        if evidence is not None and self.audit is not None and (
            self.audit.verification.evidence_fingerprint != evidence.fingerprint
        ):
            raise ValueError("publish audit verification summary does not match evidence")
        binding = self.frozen_release_binding
        if evidence is not None and binding is not None and not binding.matches_evidence(
            evidence
        ):
            raise ValueError("frozen release binding does not match verification evidence")
        if self.audit is not None and binding is not None and (
            self.audit.verification.release_binding_fingerprint != binding.fingerprint
        ):
            raise ValueError("publish audit verification summary does not match binding")
        publication_evidence = self.audit_evidence
        if publication_evidence is not None and (
            publication_evidence.bundle_fingerprint != self.bundle.fingerprint
            or (
                self.audit is not None
                and publication_evidence.publish_audit_reference
                != self.audit.audit_id
            )
        ):
            raise ValueError("publication audit evidence does not match publication")
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
        publication_aggregate: PublicationAggregate | None = None,
        accepted_assertion_manifest: AcceptedAssertionManifest | None = None,
        audit: PublishAuditRecord | None = None,
        verification_evidence: VerificationSuiteEvidence | None = None,
        production: ProductionActivationContext | None = None,
        publication_binding: PublicationDraftBinding | None = None,
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

    def verification_evidence(
        self,
        bundle_id: str,
        fingerprint: str,
    ) -> VerificationSuiteEvidence | None: ...

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
        operator_audit_reference: str | None = None,
    ) -> BundleCatalogOutcome: ...

    def record_audit_entries(
        self,
        entries: Sequence[AssemblyAuditEvidenceEntry],
        *,
        tenant_scope_fingerprint: str,
    ) -> None: ...

    def audit_entries(
        self,
        *,
        tenant_scope_fingerprint: str | None = None,
        draft_id: str | None = None,
        draft_revision_min: int | None = None,
        draft_revision_max: int | None = None,
        assertion_id: str | None = None,
        bundle_fingerprint: str | None = None,
        lifecycle_reference: str | None = None,
        predecessor_event_id: str | None = None,
        limit: int = MAX_TRAIL_ENTRIES,
        cursor: str | None = None,
    ) -> AuditTrail: ...


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
        draft_store: Any | None = None,
    ) -> None:
        self._supported_schema_versions = supported_schema_versions
        self._draft_store = draft_store
        self._publications: dict[tuple[str | None, str], tuple[BundlePublication, ...]] = {}
        self._active: dict[tuple[str | None, str], BundlePublication] = {}
        self._history: dict[tuple[str | None, str], tuple[BundlePublication, ...]] = {}
        self._audit_store: list[AssemblyAuditEvidenceEntry] = []
        self._lock = RLock()

    def authoritative_release_binding_matches(
        self,
        binding: PublicationDraftBinding,
    ) -> bool | None:
        """Preflight an exact authoritative draft when a store is configured."""
        if self._draft_store is None:
            return None
        authoritative = self._draft_store.get(
            binding.draft_id,
            tenant_scope_fingerprint=binding.tenant_scope_fingerprint,
        )
        return (
            authoritative is not None
            and authoritative.draft_revision == binding.draft_revision
            and sha256_fingerprint(authoritative.file_payload())
            == binding.draft_payload_fingerprint
        )

    def publish(
        self,
        bundle: SemanticModelBundle,
        *,
        publication_aggregate: PublicationAggregate | None = None,
        accepted_assertion_manifest: AcceptedAssertionManifest | None = None,
        audit: PublishAuditRecord | None = None,
        verification_evidence: VerificationSuiteEvidence | None = None,
        production: ProductionActivationContext | None = None,
        publication_binding: PublicationDraftBinding | None = None,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        """Validate and publish one immutable bundle version.

        When a production activation context is supplied, the bundle must
        be bound to the context's active discovery snapshot; bundles built
        from an older or unknown snapshot are rejected before publication.
        """
        if publication_binding is not None:
            if tenant_scope_fingerprint != publication_binding.tenant_scope_fingerprint:
                raise ValueError("publication binding tenant scope mismatch")
            if self._draft_store is not None:
                authoritative = self._draft_store.get(
                    publication_binding.draft_id,
                    tenant_scope_fingerprint=publication_binding.tenant_scope_fingerprint,
                )
                if authoritative is None:
                    return _failure(
                        "not_found",
                        "draft_not_found",
                        "the authoritative assembly draft does not exist",
                    )
                if (
                    authoritative.draft_revision != publication_binding.draft_revision
                    or sha256_fingerprint(authoritative.file_payload())
                    != publication_binding.draft_payload_fingerprint
                ):
                    return _failure(
                        "conflict",
                        "draft_revision_conflict",
                        "the authoritative assembly draft changed before publication",
                    )
        if publication_aggregate is not None:
            if publication_aggregate.bundle != bundle:
                return _failure(
                    "rejected",
                    "publication_aggregate_mismatch",
                    "publication aggregate does not match the published bundle",
                )
            if (
                publication_aggregate.frozen_release_binding.tenant_scope_fingerprint
                != tenant_scope_fingerprint
            ):
                return _failure(
                    "rejected",
                    "publication_aggregate_mismatch",
                    "publication aggregate tenant scope does not match the "
                    "publication scope",
                )
            records = PublicationRecordSet.from_aggregate(publication_aggregate)
        else:
            # Compatibility keyword arguments are converted into one
            # validated publication record set immediately at this
            # boundary; everything downstream of this conversion (the
            # reuse path and the stored record) sees the aggregate shape
            # only.
            try:
                records = build_publication_records(
                    bundle,
                    accepted_assertion_manifest=accepted_assertion_manifest,
                    audit=audit,
                    verification_evidence=verification_evidence,
                )
            except PublicationIntegrityError as error:
                return _failure("rejected", error.code, error.message)
            if records.frozen_release_binding is not None and (
                records.frozen_release_binding.tenant_scope_fingerprint
                != tenant_scope_fingerprint
            ):
                return _failure(
                    "rejected",
                    "verification_evidence_mismatch",
                    "verification evidence does not match the publication tenant scope",
                )
        result = validate_bundle(
            bundle,
            supported_schema_versions=self._supported_schema_versions,
            expected_snapshot_fingerprint=_expected_snapshot_fingerprint(production),
        )
        if not result.valid:
            return _failure_from_validation(result)
        evidence_reference = (
            f"verification-{records.verification_evidence.fingerprint.removeprefix('sha256:')[:24]}"
            if records.verification_evidence is not None
            else None
        )
        key = (tenant_scope_fingerprint, bundle.bundle_id)
        with self._lock:
            return self._publish_validated(
                key=key,
                bundle=bundle,
                records=records,
                evidence_reference=evidence_reference,
            )

    def _publish_validated(
        self,
        *,
        key: tuple[str | None, str],
        bundle: SemanticModelBundle,
        records: PublicationRecordSet,
        evidence_reference: str | None,
    ) -> BundleCatalogOutcome:
        """Check and append one already-validated publication under the catalog lock."""
        existing = self._publications.get(key, ())
        for publication in existing:
            if publication.bundle.fingerprint != bundle.fingerprint:
                continue
            if records.verification_evidence is not None and (
                publication.verification_evidence is None
            ):
                # An evidence-free record must never silently absorb a
                # verified publication request.
                return _failure(
                    "conflict",
                    "publication_state_conflict",
                    "existing publication has no verification evidence to reuse",
                )
            return _success(
                "reused",
                publication.bundle,
                audit_reference=(
                    publication.audit.audit_id if publication.audit is not None else None
                ),
                verification_evidence_reference=(
                    publication.audit.verification.evidence_reference
                    if publication.audit is not None
                    else None
                ),
                superseded_fingerprint=publication.supersession.predecessor_fingerprint,
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
        predecessor = existing[-1] if existing else None
        predecessor_fingerprint = (
            predecessor.bundle.fingerprint if predecessor is not None else None
        )
        record = BundlePublication(
            bundle=bundle,
            accepted_assertion_manifest=records.accepted_assertion_manifest,
            audit=records.audit,
            verification_evidence=records.verification_evidence,
            frozen_release_binding=records.frozen_release_binding,
            audit_evidence=records.audit_evidence,
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
        if records.audit_evidence is not None:
            # The publication entry links the release readiness inputs to the
            # immutable Bundle fingerprint; the predecessor publication entry
            # is linked when it exists, never fabricated for legacy records.
            predecessor_entry = (
                self._find_publication_entry(
                    key[0], predecessor_fingerprint
                )
                if predecessor_fingerprint is not None
                else None
            )
            self._append_audit_entry_locked(
                publication_audit_entry(
                    records.audit_evidence,
                    predecessor_event_ids=(
                        () if predecessor_entry is None else (predecessor_entry.event_id,)
                    ),
                )
            )
        return _success(
            "published",
            bundle,
            audit_reference=(
                records.audit.audit_id if records.audit is not None else None
            ),
            verification_evidence_reference=evidence_reference,
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

    def verification_evidence(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> VerificationSuiteEvidence | None:
        """Return immutable bounded verification evidence for a publication."""
        publication = self._publication_by_fingerprint(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )
        return publication.verification_evidence if publication is not None else None

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
        operator_audit_reference: str | None = None,
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
            operator_audit_reference=operator_audit_reference,
        )

    def activate_fingerprint(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        production: ProductionActivationContext | None = None,
        tenant_scope_fingerprint: str | None = None,
        operator_audit_reference: str | None = None,
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
            operator_audit_reference=operator_audit_reference,
        )

    @staticmethod
    def _satisfies_production_policy(
        evidence: VerificationSuiteEvidence | None,
        *,
        tenant_scope_fingerprint: str | None,
    ) -> bool:
        """Whether persisted evidence satisfies the built-in production policy."""
        from nl2data_core.verification.suite import evidence_satisfies_policy

        return (
            evidence is not None
            and evidence.policy_profile == PRODUCTION_POLICY.policy_id
            and evidence.policy_version == PRODUCTION_POLICY.policy_version
            and evidence.policy_fingerprint == PRODUCTION_POLICY.fingerprint
            and (
                tenant_scope_fingerprint is None
                or evidence.tenant_scope_fingerprint == tenant_scope_fingerprint
            )
            and evidence_satisfies_policy(evidence, policy=PRODUCTION_POLICY)
        )

    def _activate_publication(
        self,
        bundle_id: str,
        publication: BundlePublication,
        *,
        production: ProductionActivationContext | None,
        tenant_scope_fingerprint: str | None,
        operator_audit_reference: str | None = None,
        pointer_entry_kind: AuditEventKind = AuditEventKind.ACTIVATION,
    ) -> BundleCatalogOutcome:
        bundle = publication.bundle
        if publication.state is PublishedVersionState.RETIRED:
            return _failure(
                "rejected",
                "bundle_retired",
                "retired bundle versions cannot be activated",
            )
        try:
            # Activation revalidates the publication's persisted lifecycle
            # records through the centralized integrity rule set so a
            # tampered record can never be activated.
            validate_publication_integrity(
                PublicationRecordSet(
                    bundle_id=bundle.bundle_id,
                    bundle_fingerprint=bundle.fingerprint,
                    accepted_assertion_manifest=publication.accepted_assertion_manifest,
                    audit=publication.audit,
                    verification_evidence=publication.verification_evidence,
                    frozen_release_binding=publication.frozen_release_binding,
                    audit_evidence=publication.audit_evidence,
                )
            )
        except PublicationIntegrityError as error:
            return _failure("rejected", error.code, error.message)
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
            if not self._satisfies_production_policy(
                publication.verification_evidence,
                tenant_scope_fingerprint=tenant_scope_fingerprint,
            ):
                return _failure(
                    "rejected",
                    "verification_evidence_required",
                    "production activation requires passing production verification evidence",
                )
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
        prior_fingerprint = (
            previous.bundle.fingerprint if previous is not None else None
        )
        # The pointer audit entry is built and validated before the pointer
        # changes so a malformed entry can never accompany a live mutation.
        pointer_entry = self._build_pointer_audit_entry(
            key=key,
            publication=publication,
            prior_active_fingerprint=prior_fingerprint,
            resulting_active_fingerprint=bundle.fingerprint,
            pointer_entry_kind=pointer_entry_kind,
            operator_audit_reference=operator_audit_reference,
        )
        if pointer_entry is None and tenant_scope_fingerprint is not None:
            return _failure(
                "rejected",
                "audit_evidence_invalid",
                "the activation audit-evidence entry could not be created",
            )
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
        if pointer_entry is not None:
            self._append_audit_entry_locked(pointer_entry)
        return _success("activated", bundle)

    def rollback(
        self,
        bundle_id: str,
        *,
        production: ProductionActivationContext | None = None,
        tenant_scope_fingerprint: str | None = None,
        operator_audit_reference: str | None = None,
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
        # History entries keep the state at activation time; the live record
        # is authoritative for operator-managed retirement.
        target_record = self._publication_by_fingerprint(
            bundle_id,
            previous.bundle.fingerprint,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )
        if target_record is None or target_record.state is PublishedVersionState.RETIRED:
            return _failure(
                "rejected",
                "bundle_retired",
                "retired bundle versions cannot be rolled back to",
            )
        try:
            # Rollback revalidates the target's persisted lifecycle records
            # through the centralized integrity rule set so a tampered
            # record can never be restored.
            validate_publication_integrity(
                PublicationRecordSet(
                    bundle_id=bundle_id,
                    bundle_fingerprint=previous.bundle.fingerprint,
                    accepted_assertion_manifest=previous.accepted_assertion_manifest,
                    audit=previous.audit,
                    verification_evidence=previous.verification_evidence,
                    frozen_release_binding=previous.frozen_release_binding,
                )
            )
        except PublicationIntegrityError as error:
            return _failure("rejected", error.code, error.message)
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
            if not self._satisfies_production_policy(
                previous.verification_evidence,
                tenant_scope_fingerprint=tenant_scope_fingerprint,
            ):
                return _failure(
                    "rejected",
                    "verification_evidence_required",
                    "production rollback requires passing production verification evidence",
                )
        current = self._active[key]
        prior_fingerprint = current.bundle.fingerprint
        restored_fingerprint = previous.bundle.fingerprint
        rollback_entry = self._build_pointer_audit_entry(
            key=key,
            publication=previous,
            prior_active_fingerprint=prior_fingerprint,
            resulting_active_fingerprint=restored_fingerprint,
            pointer_entry_kind=AuditEventKind.ROLLBACK,
            operator_audit_reference=operator_audit_reference,
        )
        if rollback_entry is None and tenant_scope_fingerprint is not None:
            return _failure(
                "rejected",
                "audit_evidence_invalid",
                "the rollback audit-evidence entry could not be created",
            )
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
        if rollback_entry is not None:
            self._append_audit_entry_locked(rollback_entry)
        return _success("rolled_back", previous.bundle)

    def rollback_to_fingerprint(
        self,
        bundle_id: str,
        fingerprint: str,
        *,
        production: ProductionActivationContext | None = None,
        tenant_scope_fingerprint: str | None = None,
        operator_audit_reference: str | None = None,
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
            operator_audit_reference=operator_audit_reference,
            pointer_entry_kind=AuditEventKind.ROLLBACK,
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

    # -- audit-evidence storage -------------------------------------------------

    def record_audit_entries(
        self,
        entries: Sequence[AssemblyAuditEvidenceEntry],
        *,
        tenant_scope_fingerprint: str,
    ) -> None:
        """Record host-supplied lifecycle audit entries after validation.

        Tampered entries (fingerprint mismatch), cross-scope entries, and
        conflicting reuse of an existing event id are rejected before any
        write; re-recording an identical entry is idempotent.
        """
        with self._lock:
            for entry in entries:
                if entry.tenant_scope_fingerprint != tenant_scope_fingerprint:
                    raise ValueError(
                        "audit evidence entry does not match the recording scope"
                    )
                if not entry.verify_fingerprint():
                    raise ValueError("audit evidence entry fingerprint mismatch")
            for entry in entries:
                self._append_audit_entry_locked(entry)

    def audit_entries(
        self,
        *,
        tenant_scope_fingerprint: str | None = None,
        draft_id: str | None = None,
        draft_revision_min: int | None = None,
        draft_revision_max: int | None = None,
        assertion_id: str | None = None,
        bundle_fingerprint: str | None = None,
        lifecycle_reference: str | None = None,
        predecessor_event_id: str | None = None,
        limit: int = MAX_TRAIL_ENTRIES,
        cursor: str | None = None,
    ) -> AuditTrail:
        """Return one deterministic bounded page of scoped audit entries."""
        with self._lock:
            selected = [
                entry
                for entry in self._audit_store
                if (
                    tenant_scope_fingerprint is None
                    or entry.tenant_scope_fingerprint == tenant_scope_fingerprint
                )
                and (draft_id is None or entry.draft_id == draft_id)
                and (
                    draft_revision_min is None
                    or (
                        entry.draft_revision is not None
                        and entry.draft_revision >= draft_revision_min
                    )
                )
                and (
                    draft_revision_max is None
                    or (
                        entry.draft_revision is not None
                        and entry.draft_revision <= draft_revision_max
                    )
                )
                and (assertion_id is None or entry.assertion_id == assertion_id)
                and (
                    bundle_fingerprint is None
                    or entry.bundle_fingerprint == bundle_fingerprint
                )
                and (
                    lifecycle_reference is None
                    or entry.lifecycle_reference == lifecycle_reference
                )
                and (
                    predecessor_event_id is None
                    or predecessor_event_id in entry.predecessor_event_ids
                )
            ]
        return bounded_audit_trail(selected, limit=limit, cursor=cursor)

    def _build_pointer_audit_entry(
        self,
        *,
        key: tuple[str | None, str],
        publication: BundlePublication,
        prior_active_fingerprint: str | None,
        resulting_active_fingerprint: str,
        pointer_entry_kind: AuditEventKind,
        operator_audit_reference: str | None,
    ) -> AssemblyAuditEvidenceEntry | None:
        """Build the activation or rollback entry for one pointer change.

        Returns ``None`` for unscoped (global) catalogs, where a valid
        tenant-scoped entry cannot exist; legacy unscoped pointer changes
        are classified as legacy rather than fabricating evidence.
        """
        tenant_scope_fingerprint, _ = key
        if tenant_scope_fingerprint is None:
            return None
        bundle = publication.bundle
        publication_entry = self._find_publication_entry(
            tenant_scope_fingerprint, bundle.fingerprint
        )
        if publication.frozen_release_binding is not None:
            source_scope = publication.frozen_release_binding.source_scope_fingerprint
        else:
            source_scope = sha256_fingerprint(
                {"source_id": bundle.descriptor.source_id}
            )
        lifecycle_reference = self._publication_lifecycle_reference(
            publication, publication_entry
        )
        common: dict[str, Any] = {
            "tenant_scope_fingerprint": tenant_scope_fingerprint,
            "source_scope_fingerprint": source_scope,
            "event_id": self._pointer_event_id(
                pointer_entry_kind,
                bundle_id=bundle.bundle_id,
                prior_active_fingerprint=prior_active_fingerprint,
                resulting_active_fingerprint=resulting_active_fingerprint,
            ),
            "bundle_fingerprint": bundle.fingerprint,
            "lifecycle_reference": lifecycle_reference,
            "operator_audit_reference": operator_audit_reference,
            "predecessor_event_ids": (
                () if publication_entry is None else (publication_entry.event_id,)
            ),
        }
        try:
            if pointer_entry_kind is AuditEventKind.ROLLBACK:
                return rollback_audit_entry(
                    prior_active_fingerprint=prior_active_fingerprint or "",
                    restored_fingerprint=resulting_active_fingerprint,
                    **common,
                )
            return activation_audit_entry(
                resulting_active_fingerprint=resulting_active_fingerprint,
                prior_active_fingerprint=prior_active_fingerprint,
                **common,
            )
        except ValueError:
            return None

    @staticmethod
    def _pointer_event_id(
        kind: AuditEventKind,
        *,
        bundle_id: str,
        prior_active_fingerprint: str | None,
        resulting_active_fingerprint: str,
    ) -> str:
        """Deterministic event id derived from the pointer transition facts."""
        prefix = "activate" if kind is AuditEventKind.ACTIVATION else "rollback"
        return (
            prefix
            + "-"
            + sha256_fingerprint(
                {
                    "bundle_id": bundle_id,
                    "prior_active_fingerprint": prior_active_fingerprint,
                    "resulting_active_fingerprint": resulting_active_fingerprint,
                }
            ).removeprefix("sha256:")[:24]
        )

    @staticmethod
    def _publication_lifecycle_reference(
        publication: BundlePublication,
        publication_entry: AssemblyAuditEvidenceEntry | None,
    ) -> str:
        """The publish audit reference when present, else the publication entry."""
        if publication.audit is not None:
            return publication.audit.audit_id
        if publication_entry is not None:
            return publication_entry.event_id
        return (
            "publication-"
            + publication.bundle.fingerprint.removeprefix("sha256:")[:24]
        )

    def _find_publication_entry(
        self,
        tenant_scope_fingerprint: str | None,
        bundle_fingerprint: str,
    ) -> AssemblyAuditEvidenceEntry | None:
        """The recorded publication entry for one scoped immutable fingerprint."""
        if tenant_scope_fingerprint is None:
            return None
        return next(
            (
                entry
                for entry in self._audit_store
                if entry.event_kind is AuditEventKind.PUBLICATION
                and entry.tenant_scope_fingerprint == tenant_scope_fingerprint
                and entry.bundle_fingerprint == bundle_fingerprint
            ),
            None,
        )

    def _append_audit_entry_locked(self, entry: AssemblyAuditEvidenceEntry) -> None:
        """Append one validated entry under the catalog lock; idempotent."""
        existing = next(
            (
                stored
                for stored in self._audit_store
                if stored.event_id == entry.event_id
            ),
            None,
        )
        if existing is not None:
            if existing.fingerprint != entry.fingerprint:
                raise ValueError("audit evidence event id conflict")
            return
        self._audit_store.append(entry)
        self._trim_audit_entries_locked()

    def _trim_audit_entries_locked(self) -> None:
        """Bounded retention that protects active lifecycle dependencies."""
        overflow = len(self._audit_store) - _AUDIT_STORE_LIMIT
        if overflow <= 0:
            return
        protected_fingerprints = {
            publication.bundle.fingerprint
            for publications in self._publications.values()
            for publication in publications
            if publication.state is not PublishedVersionState.RETIRED
        }
        protected_ids = {
            event_id
            for entry in self._audit_store
            if entry.bundle_fingerprint in protected_fingerprints
            for event_id in (entry.event_id, *entry.predecessor_event_ids)
        }
        retained: list[AssemblyAuditEvidenceEntry] = []
        for entry in self._audit_store:
            if (
                overflow > 0
                and entry.bundle_fingerprint not in protected_fingerprints
                and entry.event_id not in protected_ids
            ):
                overflow -= 1
                continue
            retained.append(entry)
        self._audit_store = retained


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
