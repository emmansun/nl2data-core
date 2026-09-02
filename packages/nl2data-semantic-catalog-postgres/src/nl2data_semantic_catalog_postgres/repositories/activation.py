"""Activation, version, supersession, and history repository.

Pointer moves are guarded by row locks: activation locks the pointer row,
revalidates the target (core validation, production evidence, and every
declared dependency), and pushes the previous active version onto
immutable bounded history; rollback restores the top of that history.
Multi-step compositions (activate-by-fingerprint, rollback-by-fingerprint,
version state changes) remain transactions owned by the store facade -
this repository never commits independently for them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from nl2data_core.assembly.audit_evidence import (
    AssemblyAuditEvidenceEntry,
    AuditEventKind,
    activation_audit_entry,
    rollback_audit_entry,
)
from nl2data_core.bundles.catalog import (
    BundleCatalogOutcome,
    BundlePublication,
    _expected_snapshot_fingerprint,
    _failure,
    _failure_from_activation_check,
    _failure_from_validation,
    _success,
)
from nl2data_core.bundles.models import BUNDLE_SCHEMA_VERSION, SemanticModelBundle
from nl2data_core.bundles.publication import (
    LifecycleWitnessError,
    PublishedVersionState,
    SupersessionMetadata,
    validate_lifecycle_witness,
    witness_cause_type,
)
from nl2data_core.bundles.validation import validate_bundle
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.control_plane.publication.contracts import (
    PublicationRecordSet,
)
from nl2data_core.metadata.policy import ProductionActivationContext
from nl2data_core.verification.models import VerificationSuiteEvidence
from nl2data_core.verification.policy import PRODUCTION_POLICY

from ..envelope import ENVELOPE_SCHEMA_VERSION, ArtifactKind
from ..errors import SemanticCatalogError, SemanticCatalogErrorCode
from ..unit_of_work import CatalogUnitOfWork, _namespace, _parse_dt
from .audit_evidence import AuditEvidenceRepository
from .evidence import EvidenceRepository
from .publications import PublicationRepository


class ActivationRepository:
    """Active pointer, published versions, and rollback history."""

    def __init__(
        self,
        uow: CatalogUnitOfWork,
        evidence: EvidenceRepository,
        publications: PublicationRepository,
        audit: AuditEvidenceRepository,
    ) -> None:
        self._uow = uow
        self._evidence = evidence
        self._publications = publications
        self._audit = audit

    def _build_pointer_audit_entry(
        self,
        conn: Any,
        namespace: str,
        *,
        records: PublicationRecordSet,
        bundle: SemanticModelBundle,
        tenant_scope_fingerprint: str | None,
        prior_active_fingerprint: str | None,
        resulting_active_fingerprint: str,
        entry_kind: AuditEventKind,
        operator_audit_reference: str | None,
        occurred_at: datetime,
    ) -> AssemblyAuditEvidenceEntry | None:
        """Build the activation or rollback entry for one pointer change.

        Returns ``None`` for unscoped (legacy) callers, where a valid
        tenant-scoped entry cannot exist; legacy unscoped pointer changes
        are classified as legacy rather than fabricating evidence.
        """
        if tenant_scope_fingerprint is None:
            return None
        publication_entry = self._audit.find_publication_entry(
            conn, namespace, bundle.fingerprint
        )
        binding = records.frozen_release_binding
        if binding is not None:
            source_scope = binding.source_scope_fingerprint
        else:
            source_scope = sha256_fingerprint(
                {"source_id": bundle.descriptor.source_id}
            )
        if records.audit is not None:
            lifecycle_reference = records.audit.audit_id
        elif publication_entry is not None:
            lifecycle_reference = publication_entry.event_id
        else:
            lifecycle_reference = (
                "publication-"
                + bundle.fingerprint.removeprefix("sha256:")[:24]
            )
        prefix = "activate" if entry_kind is AuditEventKind.ACTIVATION else "rollback"
        event_id = (
            prefix
            + "-"
            + sha256_fingerprint(
                {
                    "bundle_id": bundle.bundle_id,
                    "prior_active_fingerprint": prior_active_fingerprint,
                    "resulting_active_fingerprint": resulting_active_fingerprint,
                }
            ).removeprefix("sha256:")[:24]
        )
        common: dict[str, Any] = {
            "tenant_scope_fingerprint": tenant_scope_fingerprint,
            "source_scope_fingerprint": source_scope,
            "event_id": event_id,
            "bundle_fingerprint": bundle.fingerprint,
            "lifecycle_reference": lifecycle_reference,
            "operator_audit_reference": operator_audit_reference,
            "predecessor_event_ids": (
                () if publication_entry is None else (publication_entry.event_id,)
            ),
            "occurred_at": occurred_at,
        }
        try:
            if entry_kind is AuditEventKind.ROLLBACK:
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

    def publication_records(
        self,
        bundle_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> tuple[BundlePublication, ...]:
        """Return bounded publication metadata in supersession order."""
        namespace = _namespace(tenant_scope_fingerprint)
        records: list[BundlePublication] = []
        with self._uow.transaction() as conn:
            rows = self._uow.execute(
                conn,
                "list_published_versions",
                (namespace, bundle_id),
            ).fetchall()
            for row in rows:
                fingerprint = row["bundle_fingerprint"]
                publication = self._uow.execute(
                    conn,
                    "read_publication_by_fingerprint",
                    (namespace, bundle_id, fingerprint),
                ).fetchone()
                if publication is None:
                    raise SemanticCatalogError(
                        SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                        "published version references a missing Bundle artifact",
                        details={"cause_type": "MissingArtifact"},
                    )
                bundle_envelope = self._uow.decode(
                    publication["envelope"],
                    ArtifactKind.BUNDLE,
                    row_schema_version=publication["schema_version"],
                )
                # Records revalidate the publication's persisted lifecycle
                # records through the centralized integrity rule set; a
                # record whose binding was stripped must fail closed
                # instead of masquerading as a legacy publication.
                persisted = self._evidence.validated_publication_records(
                    conn,
                    namespace,
                    bundle_id,
                    fingerprint,
                    audit_id=row["audit_id"],
                )
                records.append(
                    BundlePublication(
                        bundle=self._uow.bundle_from_envelope(bundle_envelope),
                        accepted_assertion_manifest=persisted.accepted_assertion_manifest,
                        audit=persisted.audit,
                        verification_evidence=persisted.verification_evidence,
                        frozen_release_binding=persisted.frozen_release_binding,
                        audit_evidence=persisted.audit_evidence,
                        state=PublishedVersionState(row["lifecycle_state"]),
                        supersession=SupersessionMetadata(
                            predecessor_fingerprint=row["predecessor_fingerprint"],
                            successor_fingerprint=row["successor_fingerprint"],
                        ),
                        published_at=_parse_dt(row["published_at"]),
                    )
                )
        return tuple(records)

    def supersession_chain(
        self,
        bundle_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> tuple[BundlePublication, ...]:
        """Return the predecessor-to-successor publication chain."""
        return self.publication_records(
            bundle_id,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )

    def versions(
        self,
        bundle_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> tuple[SemanticModelBundle, ...]:
        """Every published version of a Bundle as an immutable snapshot."""
        namespace = _namespace(tenant_scope_fingerprint)
        with self._uow.transaction() as conn:
            rows = self._uow.execute(
                conn, "list_publications", (namespace, bundle_id)
            ).fetchall()
            bundles = []
            for row in rows:
                envelope = self._uow.decode(
                    row["envelope"],
                    ArtifactKind.BUNDLE,
                    row_schema_version=row.get("schema_version"),
                )
                bundles.append(self._uow.bundle_from_envelope(envelope))
        return tuple(bundles)

    def active(
        self,
        bundle_id: str,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> SemanticModelBundle | None:
        """The active validated Bundle, or ``None`` when not activated."""
        namespace = _namespace(tenant_scope_fingerprint)
        with self._uow.transaction() as conn:
            pointer = self._uow.execute(
                conn, "read_bundle_pointer", (namespace, bundle_id)
            ).fetchone()
            if pointer is None:
                return None
            row = self._uow.execute(
                conn,
                "read_publication",
                (namespace, bundle_id, pointer["model_version"]),
            ).fetchone()
            if row is None:
                return None
            envelope = self._uow.decode(
                row["envelope"],
                ArtifactKind.BUNDLE,
                row_schema_version=row["schema_version"],
            )
            bundle = self._uow.bundle_from_envelope(envelope)
            version_row = self._uow.execute(
                conn,
                "read_published_version",
                (namespace, bundle_id, bundle.fingerprint),
            ).fetchone()
            if version_row is None:
                # The pointer is a witness that this version was activated;
                # a publication without its lifecycle row is corruption.
                raise SemanticCatalogError(
                    SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                    "active publication has no published version record",
                    details={"cause_type": "PublicationVersionMissing"},
                )
            try:
                # The pointer carries redundant fingerprint and version
                # witnesses and must agree with an ACTIVE version row; a
                # pointer that disagrees is state drift, not a legacy shape.
                validate_lifecycle_witness(
                    bundle,
                    witness="pointer",
                    witness_fingerprint=pointer["bundle_fingerprint"],
                    witness_model_version=pointer["model_version"],
                    lifecycle_state=PublishedVersionState(
                        version_row["lifecycle_state"]
                    ),
                    require_state=PublishedVersionState.ACTIVE,
                )
            except LifecycleWitnessError as error:
                raise SemanticCatalogError(
                    SemanticCatalogErrorCode.ENVELOPE_REJECTED,
                    error.message,
                    details={"cause_type": witness_cause_type(error.code)},
                ) from error
            # The active pointer is a read path too: its persisted lifecycle
            # records must satisfy the centralized integrity rule set, so a
            # tampered manifest, audit, evidence, or binding is never served
            # as the active version.  Legacy compatibility publications
            # (plain Bundle, manifest-only) carry no evidence and revalidate
            # through the Bundle envelope and manifest bundle-match alone.
            self._evidence.validated_publication_records(
                conn,
                namespace,
                bundle_id,
                bundle.fingerprint,
                audit_id=version_row["audit_id"],
            )
            return bundle

    def activate(
        self,
        conn: Any,
        bundle_id: str,
        version: str,
        *,
        namespace: str,
        now: datetime,
        production: ProductionActivationContext | None = None,
        tenant_scope_fingerprint: str | None = None,
        operator_audit_reference: str | None = None,
        entry_kind: AuditEventKind = AuditEventKind.ACTIVATION,
    ) -> BundleCatalogOutcome:
        """Point the active pointer at a published valid Bundle.

        The connection is provided by the transaction owner: the pointer row
        is locked, the target is revalidated, and only then is the pointer
        swapped with the previous active version pushed onto immutable
        history.  Any rejection preserves the current pointer.
        """
        row = self._uow.execute(
            conn,
            "read_publication",
            (namespace, bundle_id, version),
        ).fetchone()
        if row is None:
            return _failure(
                "not_found",
                "bundle_not_found",
                f"no published bundle '{bundle_id}' version '{version}' exists",
            )
        envelope = self._uow.decode(
            row["envelope"], ArtifactKind.BUNDLE, row_schema_version=row["schema_version"]
        )
        bundle = self._uow.bundle_from_envelope(envelope)
        version_row = self._uow.execute(
            conn,
            "read_published_version",
            (namespace, bundle_id, bundle.fingerprint),
        ).fetchone()
        if version_row is None:
            # Publish always writes the lifecycle row atomically with the
            # publication; a missing row is corruption, not a legacy state.
            return _failure(
                "conflict",
                "publication_version_missing",
                "published version record is missing for this publication",
            )
        if (
            PublishedVersionState(version_row["lifecycle_state"])
            is PublishedVersionState.RETIRED
        ):
            return _failure(
                "rejected",
                "bundle_retired",
                "retired bundle versions cannot be activated",
            )
        # Activation revalidates the publication's persisted lifecycle
        # records through the centralized integrity rule set so a
        # tampered record can never be activated.
        records = self._evidence.validated_publication_records(
            conn,
            namespace,
            bundle_id,
            bundle.fingerprint,
            audit_id=version_row["audit_id"],
        )
        result = validate_bundle(
            bundle,
            supported_schema_versions=(BUNDLE_SCHEMA_VERSION,),
            expected_snapshot_fingerprint=_expected_snapshot_fingerprint(production),
        )
        if not result.valid:
            return _failure_from_validation(result)
        if production is not None:
            check = production.check()
            if not check.allowed:
                return _failure_from_activation_check(check)
            evidence = self._production_evidence(
                conn,
                namespace,
                bundle_id,
                bundle.fingerprint,
                tenant_scope_fingerprint=tenant_scope_fingerprint,
            )
            if evidence is None:
                return _failure(
                    "rejected",
                    "verification_evidence_required",
                    "production activation requires passing production verification evidence",
                )
        for dependency in bundle.dependencies:
            dep = self._uow.execute(
                conn,
                "read_publication_fingerprint",
                (namespace, dependency.bundle_id, dependency.version),
            ).fetchone()
            if dep is None or dep["bundle_fingerprint"] != dependency.fingerprint:
                return _failure(
                    "rejected",
                    "dependency_unavailable",
                    f"dependency '{dependency.dependency_id}' is unavailable "
                    "or has a different fingerprint",
                    member_id=dependency.dependency_id,
                )
        pointer = self._uow.execute(
            conn, "lock_bundle_pointer", (namespace, bundle_id)
        ).fetchone()
        if pointer is None:
            # A missing pointer means "never activated"; any ACTIVE version
            # row alongside it is state drift (the pointer row was lost),
            # and activating here would mint a second ACTIVE lifecycle row.
            orphans = self._uow.execute(
                conn, "list_published_versions", (namespace, bundle_id)
            ).fetchall()
            if any(
                row["lifecycle_state"] == PublishedVersionState.ACTIVE.value
                for row in orphans
            ):
                return _failure(
                    "conflict",
                    "orphan_active_version",
                    "an active version exists without an active pointer; "
                    "the lifecycle state is corrupt",
                )
        elif pointer["model_version"] == version:
            self._uow.execute(
                conn,
                "set_published_version_state",
                (
                    PublishedVersionState.ACTIVE.value,
                    namespace,
                    bundle_id,
                    bundle.fingerprint,
                ),
            )
            return _success("activated", bundle)
        # The activation entry is built before any pointer mutation and
        # recorded only once the move succeeds; a scoped caller whose
        # entry cannot be built is rejected instead of moving silently.
        pointer_entry = self._build_pointer_audit_entry(
            conn,
            namespace,
            records=records,
            bundle=bundle,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
            prior_active_fingerprint=(
                pointer["bundle_fingerprint"] if pointer is not None else None
            ),
            resulting_active_fingerprint=bundle.fingerprint,
            entry_kind=entry_kind,
            operator_audit_reference=operator_audit_reference,
            occurred_at=now,
        )
        if pointer_entry is None and tenant_scope_fingerprint is not None:
            return _failure(
                "rejected",
                "audit_evidence_invalid",
                "activation audit evidence could not be built for this scope",
            )
        position = int(
            self._uow.execute(
                conn, "next_history_position", (namespace, bundle_id)
            ).fetchone()["next_position"]
        )
        if pointer is not None:
            self._uow.execute(
                conn,
                "insert_history",
                (
                    namespace,
                    bundle_id,
                    position,
                    pointer["model_version"],
                    pointer["bundle_fingerprint"],
                    pointer["schema_version"],
                    _parse_dt(pointer["activated_at"]),
                    now,
                ),
            )
            self._uow.execute(
                conn,
                "set_published_version_state",
                (
                    PublishedVersionState.SUPERSEDED.value,
                    namespace,
                    bundle_id,
                    pointer["bundle_fingerprint"],
                ),
            )
        self._uow.execute(
            conn,
            "upsert_bundle_pointer",
            (
                namespace,
                bundle_id,
                bundle.model_version,
                bundle.fingerprint,
                ENVELOPE_SCHEMA_VERSION,
                now,
                # A first-ever activation has no predecessor to record, so
                # its pointer sits at sequence 0; any activation that pushed
                # a history row sits exactly at that row's position.  An
                # empty history beside a sequence >= 1 is therefore always
                # a discontinuity, never a legitimate "no history" state.
                position if pointer is not None else 0,
            ),
        )
        self._uow.execute(
            conn,
            "set_published_version_state",
            (
                PublishedVersionState.ACTIVE.value,
                namespace,
                bundle_id,
                bundle.fingerprint,
            ),
        )
        trim_below = position - self._uow.config.max_bundle_history + 1
        if trim_below > 1:
            self._uow.execute(
                conn,
                "trim_history",
                (namespace, bundle_id, trim_below),
            )
        if pointer_entry is not None:
            self._audit.insert_audit_entries(conn, namespace, [pointer_entry])
        self._uow.insert_event(
            conn,
            "bundle_activated",
            bundle_id,
            namespace=namespace,
            occurred_at=now,
        )
        return _success("activated", bundle)

    def _production_evidence(
        self,
        conn: Any,
        namespace: str,
        bundle_id: str,
        fingerprint: str,
        *,
        tenant_scope_fingerprint: str | None,
    ) -> VerificationSuiteEvidence | None:
        """Load fully validated evidence and check the production policy.

        The immutable frozen-binding/audit/manifest cross-link validation
        runs first and fails closed on tampered or legacy evidence; the
        caller's tenant scope must match the evidence scope when supplied.
        Returns ``None`` when the evidence does not satisfy the production
        policy.
        """
        from nl2data_core.verification.suite import evidence_satisfies_policy

        evidence = self._evidence.validated_verification_evidence(
            conn, namespace, bundle_id, fingerprint
        )
        if evidence is None:
            return None
        if (
            tenant_scope_fingerprint is not None
            and evidence.tenant_scope_fingerprint != tenant_scope_fingerprint
        ):
            return None
        if (
            evidence.policy_profile != PRODUCTION_POLICY.policy_id
            or evidence.policy_version != PRODUCTION_POLICY.policy_version
            or evidence.policy_fingerprint != PRODUCTION_POLICY.fingerprint
            or not evidence_satisfies_policy(evidence, policy=PRODUCTION_POLICY)
        ):
            return None
        return evidence

    def rollback(
        self,
        conn: Any,
        bundle_id: str,
        *,
        namespace: str,
        now: datetime,
        production: ProductionActivationContext | None = None,
        tenant_scope_fingerprint: str | None = None,
        operator_audit_reference: str | None = None,
    ) -> BundleCatalogOutcome:
        """Move the active pointer to the previous active version.

        Published artifacts are never mutated or deleted; only the pointer
        changes, and rollback is possible only while a prior active version
        exists and still revalidates.
        """
        pointer = self._uow.execute(
            conn, "lock_bundle_pointer", (namespace, bundle_id)
        ).fetchone()
        if pointer is None:
            return _failure(
                "not_found",
                "bundle_not_active",
                f"bundle '{bundle_id}' has no active version",
            )
        top = self._uow.execute(
            conn, "read_history_top", (namespace, bundle_id)
        ).fetchone()
        if top is None:
            if int(pointer["activation_sequence"]) >= 1:
                # The pointer's activation sequence witnesses that the
                # current activation pushed a history row; an empty history
                # beside it is deleted state, not a legacy "never activated"
                # shape (first-ever activations sit at sequence 0).
                return _failure(
                    "rejected",
                    "history_discontinuity",
                    f"bundle '{bundle_id}' rollback history is missing "
                    "although the active pointer records a previous activation",
                )
            return _failure(
                "no_history",
                "no_rollback_history",
                f"bundle '{bundle_id}' has no previously active version",
            )
        row = self._uow.execute(
            conn,
            "read_publication",
            (namespace, bundle_id, top["model_version"]),
        ).fetchone()
        if row is None:
            return _failure(
                "rejected",
                "rollback_target_unavailable",
                f"bundle '{bundle_id}' rollback target is no longer published",
            )
        envelope = self._uow.decode(
            row["envelope"], ArtifactKind.BUNDLE, row_schema_version=row["schema_version"]
        )
        target = self._uow.bundle_from_envelope(envelope)
        version_row = self._uow.execute(
            conn,
            "read_published_version",
            (namespace, bundle_id, target.fingerprint),
        ).fetchone()
        if version_row is None:
            return _failure(
                "conflict",
                "publication_version_missing",
                "published version record is missing for this publication",
            )
        try:
            # The history row carries the fingerprint and version of the
            # record it activated; a disagreement is corruption, and the
            # version row must not be retired.  The top row must also sit
            # exactly at the pointer's activation sequence: history rows
            # are numbered by the activation that pushed them, so a lower
            # position means the newest row was deleted and rolling back
            # to the top would silently skip the version it recorded.
            # Both checks live in the shared lifecycle witness validator.
            validate_lifecycle_witness(
                target,
                witness="history",
                witness_fingerprint=top["bundle_fingerprint"],
                witness_model_version=top["model_version"],
                lifecycle_state=PublishedVersionState(
                    version_row["lifecycle_state"]
                ),
                witness_position=int(top["position"]),
                expected_position=int(pointer["activation_sequence"]),
            )
        except LifecycleWitnessError as error:
            return _failure("rejected", error.code, error.message)
        # Rollback revalidates the target's persisted lifecycle records
        # through the centralized integrity rule set so a tampered
        # record can never be restored.
        records = self._evidence.validated_publication_records(
            conn,
            namespace,
            bundle_id,
            target.fingerprint,
            audit_id=version_row["audit_id"],
        )
        if production is not None:
            check = production.check()
            if not check.allowed:
                return _failure_from_activation_check(check)
            result = validate_bundle(
                target,
                supported_schema_versions=(BUNDLE_SCHEMA_VERSION,),
                expected_snapshot_fingerprint=_expected_snapshot_fingerprint(
                    production
                ),
            )
            if not result.valid:
                return _failure_from_validation(result)
            evidence = self._production_evidence(
                conn,
                namespace,
                bundle_id,
                target.fingerprint,
                tenant_scope_fingerprint=tenant_scope_fingerprint,
            )
            if evidence is None:
                return _failure(
                    "rejected",
                    "verification_evidence_required",
                    "production rollback requires passing production verification evidence",
                )
        # The rollback entry is built before any pointer mutation and
        # recorded only once the move succeeds; both versions stay
        # explainable with their prior and restored fingerprints.
        rollback_entry = self._build_pointer_audit_entry(
            conn,
            namespace,
            records=records,
            bundle=target,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
            prior_active_fingerprint=pointer["bundle_fingerprint"],
            resulting_active_fingerprint=target.fingerprint,
            entry_kind=AuditEventKind.ROLLBACK,
            operator_audit_reference=operator_audit_reference,
            occurred_at=now,
        )
        if rollback_entry is None and tenant_scope_fingerprint is not None:
            return _failure(
                "rejected",
                "audit_evidence_invalid",
                "rollback audit evidence could not be built for this scope",
            )
        self._uow.execute(
            conn,
            "upsert_bundle_pointer",
            (
                namespace,
                bundle_id,
                target.model_version,
                target.fingerprint,
                ENVELOPE_SCHEMA_VERSION,
                now,
                # The restored version takes over the activation sequence
                # slot below the consumed history row, keeping the
                # "history ends at the activation sequence" invariant so
                # the next rollback revalidates continuity against it.
                int(top["position"]) - 1,
            ),
        )
        self._uow.execute(
            conn,
            "set_published_version_state",
            (
                PublishedVersionState.SUPERSEDED.value,
                namespace,
                bundle_id,
                pointer["bundle_fingerprint"],
            ),
        )
        self._uow.execute(
            conn,
            "set_published_version_state",
            (
                PublishedVersionState.ACTIVE.value,
                namespace,
                bundle_id,
                target.fingerprint,
            ),
        )
        self._uow.execute(
            conn,
            "delete_history_top",
            (namespace, bundle_id, top["position"]),
        )
        if rollback_entry is not None:
            self._audit.insert_audit_entries(conn, namespace, [rollback_entry])
        self._uow.insert_event(
            conn,
            "bundle_rolled_back",
            bundle_id,
            namespace=namespace,
            occurred_at=now,
        )
        return _success("rolled_back", target)

    def set_version_state(
        self,
        bundle_id: str,
        fingerprint: str,
        state: PublishedVersionState,
        *,
        tenant_scope_fingerprint: str | None = None,
    ) -> BundleCatalogOutcome:
        """Persist operator-managed deprecation or retirement metadata."""
        if state not in {
            PublishedVersionState.DEPRECATED,
            PublishedVersionState.RETIRED,
        }:
            raise ValueError("state must be deprecated or retired")
        bundle = self._publications.get_by_fingerprint(
            bundle_id,
            fingerprint,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )
        if bundle is None:
            return _failure(
                "not_found", "bundle_not_found", "published bundle was not found"
            )
        active = self.active(
            bundle_id,
            tenant_scope_fingerprint=tenant_scope_fingerprint,
        )
        if (
            state is PublishedVersionState.RETIRED
            and active is not None
            and active.fingerprint == fingerprint
        ):
            return _failure(
                "rejected",
                "active_bundle_retirement",
                "the active bundle cannot be retired",
            )
        namespace = _namespace(tenant_scope_fingerprint)
        with self._uow.transaction() as conn:
            self._uow.execute(
                conn,
                "set_published_version_state",
                (state.value, namespace, bundle_id, fingerprint),
            )
        if state is PublishedVersionState.DEPRECATED:
            return _success("deprecated", bundle)
        return _success("retired", bundle)

__all__ = ["ActivationRepository"]
