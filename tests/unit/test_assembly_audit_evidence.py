"""Assembly audit-evidence contract tests.

Covers the bounded audit-evidence entry model (identity excluding
presentation metadata, subject consistency, predecessor links, safe
fields, deterministic bounded trail ordering), the lifecycle record
factories that keep review, approval, lint, verification, publication,
activation, and rollback evidence outside the Bundle fingerprint
domain, and the publication audit-evidence cross-link validation
against every immutable publication identity.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from nl2data_core.assembly import (
    ASSEMBLY_API_VERSION,
    AssemblyAuditEvidenceEntry,
    AssemblyDraft,
    AssemblyState,
    AssertionProvenance,
    AuditEventKind,
    AuditOutcome,
    AuditPayloadBindings,
    AuditSubjectKind,
    AuditTrail,
    PublicationAuditEvidence,
    bounded_audit_trail,
    order_audit_entries,
)
from nl2data_core.assembly.audit_evidence import (
    activation_audit_entry,
    assertion_decision_audit_entry,
    draft_lifecycle_audit_entry,
    lint_reference_audit_entry,
    publication_audit_entry,
    rollback_audit_entry,
    verification_reference_audit_entry,
)
from nl2data_core.assembly.lifecycle import (
    AssertionDecisionKind,
    AssertionDecisionRecord,
    DraftLifecycleAction,
    DraftLifecycleRecord,
)
from nl2data_core.assembly.manifest import AcceptedAssertionManifest
from nl2data_core.bundles import (
    AssertionProvenanceSummary,
    DeploymentBindingRedactionSummary,
    PublishAuditRecord,
    PublishIdempotencyStatus,
    PublishVerificationSummary,
)
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.control_plane.publication.contracts import (
    FrozenReleaseBinding,
    PublicationIntegrityError,
    PublicationRecordSet,
    publication_audit_evidence_classification,
    validate_publication_integrity,
)
from nl2data_core.verification import (
    COMPATIBILITY_POLICY,
    VerificationLayerEvidence,
    VerificationSuiteEvidence,
)

TENANT = "sha256:" + "a" * 64
SOURCE = "sha256:" + "b" * 64
FP_A = "sha256:" + "c" * 64
FP_B = "sha256:" + "d" * 64
BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _entry(**overrides: object) -> AssemblyAuditEvidenceEntry:
    values: dict[str, object] = {
        "event_id": "event-1",
        "event_kind": AuditEventKind.DRAFT_APPROVAL,
        "subject_kind": AuditSubjectKind.DRAFT,
        "subject_reference": "draft-sales",
        "tenant_scope_fingerprint": TENANT,
        "source_scope_fingerprint": SOURCE,
        "draft_id": "draft-sales",
        "occurred_at": BASE,
    }
    values.update(overrides)
    return AssemblyAuditEvidenceEntry(**values)


# -- 1.1: entry identity, bounds, and trail primitives -------------------------


class TestEntryIdentityAndBounds:
    def test_fingerprint_excludes_occurred_at_presentation_metadata(self) -> None:
        """Entry identity is stable across clock skew between workers."""
        first = _entry()
        second = _entry(occurred_at=BASE + timedelta(hours=5))
        assert first.fingerprint == second.fingerprint
        assert first.fingerprint == sha256_fingerprint(first.canonical_payload())
        assert first.verify_fingerprint()
        assert "occurred_at" not in first.canonical_payload()

    def test_tampered_entry_fails_the_fingerprint_witness(self) -> None:
        entry = _entry()
        tampered = entry.model_copy(update={"reason": "backdated approval"})
        assert not tampered.verify_fingerprint()

    def test_predecessor_links_are_sorted_unique_and_bounded(self) -> None:
        entry = _entry(predecessor_event_ids=("event-9", "event-2"))
        assert entry.predecessor_event_ids == ("event-2", "event-9")
        with pytest.raises(ValidationError):
            _entry(predecessor_event_ids=("event-2", "event-2"))
        with pytest.raises(ValidationError):
            _entry(predecessor_event_ids=(f"event-{index}" for index in range(9)))
        with pytest.raises(ValidationError):
            _entry(predecessor_event_ids=("event-1",))

    def test_subject_consistency_is_enforced_per_event_kind(self) -> None:
        # Assertion decisions require an assertion and draft reference and
        # must be filed under the assertion subject.
        with pytest.raises(ValidationError):
            _entry(
                event_kind=AuditEventKind.ASSERTION_APPROVAL,
                subject_kind=AuditSubjectKind.ASSERTION,
                subject_reference=FP_A,
            )
        with pytest.raises(ValidationError):
            _entry(
                event_kind=AuditEventKind.ASSERTION_APPROVAL,
                subject_kind=AuditSubjectKind.DRAFT,
                subject_reference=FP_A,
                assertion_id=FP_A,
            )
        # Publication, activation, and rollback entries are filed under
        # their own immutable artifact subject with a lifecycle reference.
        base = {
            "event_kind": AuditEventKind.PUBLICATION,
            "subject_kind": AuditSubjectKind.PUBLICATION,
            "subject_reference": "publish-1",
        }
        with pytest.raises(ValidationError):
            _entry(**base)
        with pytest.raises(ValidationError):
            _entry(
                event_kind=AuditEventKind.PUBLICATION,
                subject_kind=AuditSubjectKind.BUNDLE,
                subject_reference="publish-1",
                bundle_fingerprint=FP_A,
                lifecycle_reference="publish-1",
            )
        assert _entry(
            **base, bundle_fingerprint=FP_A, lifecycle_reference="publish-1"
        ).fingerprint.startswith("sha256:")

    def test_unsafe_or_unbounded_field_values_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _entry(operator_audit_reference="operator with password=secret")
        with pytest.raises(ValidationError):
            _entry(status_code="Not A Status Code")
        with pytest.raises(ValidationError):
            _entry(reason="x" * 1025)
        # Payload bindings are typed: untyped dictionaries are impossible.
        with pytest.raises(ValidationError):
            AuditPayloadBindings(query="select 1")

    def test_trail_ordering_is_deterministic_and_bounded(self) -> None:
        entries = [
            _entry(event_id=event_id, occurred_at=BASE + timedelta(minutes=offset))
            for offset, event_id in enumerate(("event-3", "event-1", "event-2"))
        ]
        ordered = order_audit_entries(entries)
        assert [entry.event_id for entry in ordered] == [
            "event-3",
            "event-1",
            "event-2",
        ]
        page = bounded_audit_trail(entries, limit=2)
        assert [entry.event_id for entry in page.entries] == ["event-3", "event-1"]
        assert page.total_count == 3
        assert page.has_more
        assert page.next_cursor == "event-1"
        rest = bounded_audit_trail(entries, limit=2, cursor=page.next_cursor)
        assert [entry.event_id for entry in rest.entries] == ["event-2"]
        assert not rest.has_more
        assert rest.next_cursor is None
        # Unknown or pruned cursors restart from the beginning.
        restarted = bounded_audit_trail(entries, limit=2, cursor="event-missing")
        assert restarted.total_count == 3
        with pytest.raises(ValueError):
            bounded_audit_trail(entries, limit=0)
        with pytest.raises(ValueError):
            bounded_audit_trail(entries, limit=201)
        with pytest.raises(ValidationError):
            AuditTrail(entries=ordered[:2], next_cursor="event-1", has_more=False)
        with pytest.raises(ValidationError):
            AuditTrail(entries=ordered[:2], next_cursor="event-3", has_more=True)

    def test_redacted_projection_stays_bounded(self) -> None:
        entry = _entry(reason="short reason")
        payload = entry.safe_payload()
        assert payload["reason"] == "short reason"
        assert json.dumps(payload)


# -- 1.2: lifecycle factories stay outside Bundle fingerprints -----------------


class TestLifecycleFactories:
    def test_assertion_decisions_bind_review_facts(self) -> None:
        record = AssertionDecisionRecord(
            draft_id="draft-sales",
            action=AssertionDecisionKind.APPROVE,
            assertion_id=FP_A,
            resulting_assertion_id=FP_B,
            operator_reference="reviewer-1",
            previous_payload_hash=FP_A,
            resulting_payload_hash=FP_B,
            previous_provenance=AssertionProvenance(kind="manual"),
        )
        entry = assertion_decision_audit_entry(
            record,
            tenant_scope_fingerprint=TENANT,
            source_scope_fingerprint=SOURCE,
            event_id="assertion-approve-1",
            draft_revision=3,
        )
        assert entry.event_kind is AuditEventKind.ASSERTION_APPROVAL
        assert entry.outcome is AuditOutcome.SUCCEEDED
        assert entry.subject_kind is AuditSubjectKind.ASSERTION
        assert entry.assertion_id == FP_A
        assert entry.draft_id == "draft-sales"
        assert entry.payload_bindings.reviewed_payload_hash == FP_A
        assert entry.payload_bindings.resulting_payload_hash == FP_B
        rejected = record.model_copy(update={"action": AssertionDecisionKind.REJECT})
        entry = assertion_decision_audit_entry(
            rejected,
            tenant_scope_fingerprint=TENANT,
            source_scope_fingerprint=SOURCE,
            event_id="assertion-reject-1",
        )
        assert entry.event_kind is AuditEventKind.ASSERTION_REVIEW
        assert entry.outcome is AuditOutcome.REJECTED

    def test_draft_lifecycle_records_classify_submit_and_approve(self) -> None:
        submitted = DraftLifecycleRecord(
            draft_id="draft-sales",
            action=DraftLifecycleAction.SUBMIT_FOR_REVIEW,
            operator_reference="author-1",
            previous_revision=0,
            resulting_revision=1,
        )
        entry = draft_lifecycle_audit_entry(
            submitted,
            tenant_scope_fingerprint=TENANT,
            source_scope_fingerprint=SOURCE,
            event_id="draft-submit-1",
        )
        assert entry.event_kind is AuditEventKind.AUTHORING_IMPORT
        assert entry.subject_kind is AuditSubjectKind.DRAFT
        assert entry.draft_revision == 1
        approved = submitted.model_copy(
            update={
                "action": DraftLifecycleAction.APPROVE,
                "previous_revision": 1,
                "resulting_revision": 2,
            }
        )
        entry = draft_lifecycle_audit_entry(
            approved,
            tenant_scope_fingerprint=TENANT,
            source_scope_fingerprint=SOURCE,
            event_id="draft-approve-1",
        )
        assert entry.event_kind is AuditEventKind.DRAFT_APPROVAL

    def test_lint_and_verification_references_stay_draft_scoped(self) -> None:
        lint = lint_reference_audit_entry(
            tenant_scope_fingerprint=TENANT,
            source_scope_fingerprint=SOURCE,
            event_id="lint-1",
            draft_id="draft-sales",
            draft_revision=2,
            lint_reference="lint-run-1",
        )
        assert lint.event_kind is AuditEventKind.LINT_REFERENCE
        assert lint.subject_kind is AuditSubjectKind.DRAFT
        assert lint.payload_bindings.lint_reference == "lint-run-1"
        verification = verification_reference_audit_entry(
            tenant_scope_fingerprint=TENANT,
            source_scope_fingerprint=SOURCE,
            event_id="verification-1",
            draft_id="draft-sales",
            draft_revision=3,
            evidence_fingerprint=FP_A,
            policy_profile=COMPATIBILITY_POLICY.policy_id,
            policy_version=COMPATIBILITY_POLICY.policy_version,
        )
        assert verification.event_kind is AuditEventKind.VERIFICATION_REFERENCE
        assert verification.payload_bindings.evidence_fingerprint == FP_A

    def test_pointer_entries_link_publication_evidence(self) -> None:
        binding = PublicationAuditEvidence(
            approved_draft_id="draft-sales",
            approved_draft_revision=3,
            bundle_fingerprint=FP_A,
            manifest_fingerprint=FP_B,
            verification_evidence_fingerprint=FP_B,
            tenant_scope_fingerprint=TENANT,
            source_scope_fingerprint=SOURCE,
            policy_profile=COMPATIBILITY_POLICY.policy_id,
            policy_version=COMPATIBILITY_POLICY.policy_version,
            policy_fingerprint=COMPATIBILITY_POLICY.fingerprint,
            separation_mode="strict",
            separation_allowed=True,
            publish_audit_reference="publish-1",
        )
        publication = publication_audit_entry(binding)
        assert publication.event_id == binding.publication_event_id()
        assert publication.event_id == (
            "publish-" + binding.fingerprint.removeprefix("sha256:")[:24]
        )
        assert publication.lifecycle_reference == "publish-1"
        assert publication.bundle_fingerprint == FP_A
        activation = activation_audit_entry(
            tenant_scope_fingerprint=TENANT,
            source_scope_fingerprint=SOURCE,
            event_id="activate-1",
            bundle_fingerprint=FP_A,
            lifecycle_reference="publish-1",
            resulting_active_fingerprint=FP_A,
            predecessor_event_ids=(publication.event_id,),
        )
        assert activation.event_kind is AuditEventKind.ACTIVATION
        assert activation.subject_kind is AuditSubjectKind.ACTIVATION
        assert activation.predecessor_event_ids == (publication.event_id,)
        assert activation.payload_bindings.prior_active_fingerprint is None
        rollback = rollback_audit_entry(
            tenant_scope_fingerprint=TENANT,
            source_scope_fingerprint=SOURCE,
            event_id="rollback-1",
            bundle_fingerprint=FP_A,
            lifecycle_reference="publish-1",
            prior_active_fingerprint=FP_B,
            restored_fingerprint=FP_A,
            predecessor_event_ids=(publication.event_id,),
        )
        assert rollback.event_kind is AuditEventKind.ROLLBACK
        assert rollback.payload_bindings.prior_active_fingerprint == FP_B
        assert rollback.payload_bindings.resulting_active_fingerprint == FP_A

    def test_entries_never_enter_the_bundle_fingerprint_domain(self) -> None:
        """Evidence identity derives from its own facts, never the reverse."""
        bundle_fingerprint = sha256_fingerprint({"bundle_fingerprint": FP_A})
        entry = activation_audit_entry(
            tenant_scope_fingerprint=TENANT,
            source_scope_fingerprint=SOURCE,
            event_id="activate-1",
            bundle_fingerprint=FP_A,
            lifecycle_reference="publish-1",
            resulting_active_fingerprint=FP_A,
            occurred_at=BASE,
        )
        # The entry fingerprint is its own identity over its own payload;
        # the Bundle fingerprint is an input fact, never an output.
        assert entry.fingerprint == sha256_fingerprint(entry.canonical_payload())
        assert entry.fingerprint != FP_A
        assert entry.fingerprint != bundle_fingerprint
        # The bundle identity is unchanged by recording evidence, and the
        # canonical payload carries no semantic content, prompts, queries,
        # or credential material.
        assert bundle_fingerprint == sha256_fingerprint({"bundle_fingerprint": FP_A})
        serialized = json.dumps(entry.canonical_payload())
        for forbidden in ("password", "prompt", "query", "sql", "credential"):
            assert forbidden not in serialized


# -- 1.3: publication audit-evidence cross-links -------------------------------


def _bundle_parts():
    from nl2data_core.bundles import (
        BundleProvenance,
        BundleQualityStatus,
        SemanticModelBundle,
        SemanticSourceReference,
    )
    from nl2data_core.views import (
        SemanticDescriptor,
        SemanticEntityDescriptor,
        SemanticFieldDescriptor,
    )

    descriptor = SemanticDescriptor(
        descriptor_id="sales",
        version=1,
        source_id="sales",
        entities=(
            SemanticEntityDescriptor(
                entity_id="orders",
                label="Orders",
                fields=(
                    SemanticFieldDescriptor(
                        field_id="amount", label="Amount", data_type="float"
                    ),
                ),
            ),
        ),
    )
    bundle = SemanticModelBundle(
        bundle_id="sales_model",
        model_version="1.0.0",
        descriptor=descriptor,
        sources=(SemanticSourceReference(reference_id="sales", source_id="sales"),),
        provenance=BundleProvenance(
            owner_reference="team-analytics", quality=BundleQualityStatus.APPROVED
        ),
    )
    draft = AssemblyDraft(
        apiVersion=ASSEMBLY_API_VERSION,
        draft_id="draft-sales",
        bundle_id=bundle.bundle_id,
        source_id=bundle.descriptor.source_id,
        model_version=bundle.model_version,
        state=AssemblyState.APPROVED,
        draft_revision=3,
        author_reference="author-1",
    )
    return bundle, draft


class TestPublicationAuditEvidenceCrossLinks:
    @staticmethod
    def _verified_parts():
        bundle, draft = _bundle_parts()
        manifest = AcceptedAssertionManifest.from_draft(
            draft, bundle_fingerprint=bundle.fingerprint
        )
        evidence = VerificationSuiteEvidence(
            status="passed",
            policy_profile=COMPATIBILITY_POLICY.policy_id,
            policy_version=COMPATIBILITY_POLICY.policy_version,
            policy_fingerprint=COMPATIBILITY_POLICY.fingerprint,
            runner_id="suite-runner",
            runner_version=1,
            draft_id=draft.draft_id,
            draft_revision=draft.draft_revision,
            bundle_fingerprint=bundle.fingerprint,
            manifest_fingerprint=sha256_fingerprint(manifest.canonical_payload()),
            tenant_scope_fingerprint=TENANT,
            source_scope_fingerprint=SOURCE,
            layers=(VerificationLayerEvidence(layer="layer_1", status="passed"),),
        )
        binding = FrozenReleaseBinding.from_evidence(evidence)
        reference = f"verification-{evidence.fingerprint.removeprefix('sha256:')[:24]}"
        audit = PublishAuditRecord(
            audit_id="publish-1",
            bundle_id=bundle.bundle_id,
            bundle_fingerprint=bundle.fingerprint,
            approval_chain=("author-1", "reviewer-1", "publisher-1"),
            assertion_provenance=AssertionProvenanceSummary(manual=1),
            verification=PublishVerificationSummary(
                structural_valid=True,
                manifest_equivalent=True,
                host_callback_count=1,
                suite_version=evidence.suite_version,
                policy_profile=evidence.policy_profile,
                policy_version=evidence.policy_version,
                policy_fingerprint=evidence.policy_fingerprint,
                runner_id=evidence.runner_id,
                runner_version=evidence.runner_version,
                layer_statuses=tuple(layer.status.value for layer in evidence.layers),
                layer_case_counts=tuple(len(layer.cases) for layer in evidence.layers),
                evidence_fingerprint=evidence.fingerprint,
                evidence_reference=reference,
                release_binding_fingerprint=binding.fingerprint,
            ),
            idempotency_status=PublishIdempotencyStatus.CREATED,
            deployment_bindings=DeploymentBindingRedactionSummary(),
            separation_mode="strict",
            separation_reason_code="authorized",
        )
        return bundle, manifest, evidence, binding, audit

    @staticmethod
    def _binding(evidence, audit, **overrides: object) -> PublicationAuditEvidence:
        values: dict[str, object] = {
            "approved_draft_id": evidence.draft_id,
            "approved_draft_revision": evidence.draft_revision,
            "approved_plan_fingerprint": evidence.plan_fingerprint,
            "bundle_fingerprint": evidence.bundle_fingerprint,
            "manifest_fingerprint": evidence.manifest_fingerprint,
            "verification_evidence_fingerprint": evidence.fingerprint,
            "tenant_scope_fingerprint": evidence.tenant_scope_fingerprint,
            "source_scope_fingerprint": evidence.source_scope_fingerprint,
            "policy_profile": evidence.policy_profile,
            "policy_version": evidence.policy_version,
            "policy_fingerprint": evidence.policy_fingerprint,
            "separation_mode": "strict",
            "separation_allowed": True,
            "separation_reason_code": "authorized",
            "publish_audit_reference": audit.audit_id,
        }
        values.update(overrides)
        return PublicationAuditEvidence(**values)

    @staticmethod
    def _complete_records(override: dict[str, object] | None = None):
        """A fully verified record set with a consistent audit binding."""
        bundle, manifest, evidence, binding, audit = (
            TestPublicationAuditEvidenceCrossLinks._verified_parts()
        )
        return (
            PublicationRecordSet(
                bundle_id=bundle.bundle_id,
                bundle_fingerprint=bundle.fingerprint,
                accepted_assertion_manifest=manifest,
                audit=audit,
                verification_evidence=evidence,
                frozen_release_binding=binding,
                audit_evidence=TestPublicationAuditEvidenceCrossLinks._binding(
                    evidence, audit, **(override or {})
                ),
            ),
            evidence,
            audit,
            bundle,
            manifest,
            binding,
        )

    def test_consistent_binding_passes_and_classifies_complete(self) -> None:
        records, _evidence, _audit, _bundle, _manifest, _binding = (
            self._complete_records()
        )
        validate_publication_integrity(records)
        assert publication_audit_evidence_classification(records) == "complete"
        # A verified record set predating audit-evidence bindings classifies
        # as legacy, never as incomplete.
        legacy = records.model_copy(update={"audit_evidence": None})
        validate_publication_integrity(legacy)
        assert publication_audit_evidence_classification(legacy) == "legacy"

    def test_mismatched_binding_identity_fails_closed(self) -> None:
        overrides = (
            {"bundle_fingerprint": FP_B},
            {"publish_audit_reference": "publish-other"},
            {"verification_evidence_fingerprint": FP_B},
            {"manifest_fingerprint": FP_B},
            {"approved_draft_id": "draft-other"},
            {"approved_draft_revision": 99},
            {"approved_plan_fingerprint": FP_B},
            {"policy_profile": "other-policy"},
            {"policy_version": 9},
            {"policy_fingerprint": FP_B},
            {"tenant_scope_fingerprint": FP_B},
            {"source_scope_fingerprint": FP_B},
        )
        for override in overrides:
            records, _evidence, _audit, _bundle, _manifest, _binding = (
                self._complete_records(override)
            )
            with pytest.raises(PublicationIntegrityError) as excinfo:
                validate_publication_integrity(records)
            assert excinfo.value.code == "publication_audit_evidence_mismatch", (
                override
            )

    def test_binding_without_its_publication_records_fails_closed(self) -> None:
        """Evidence without the records it explains never degrades to legacy."""
        _records, evidence, audit, bundle, manifest, binding = (
            self._complete_records()
        )
        audit_evidence = self._binding(evidence, audit)
        for update in (
            {"audit": None},
            {"verification_evidence": None},
            {"accepted_assertion_manifest": None},
        ):
            values: dict[str, object] = {
                "bundle_id": bundle.bundle_id,
                "bundle_fingerprint": bundle.fingerprint,
                "accepted_assertion_manifest": manifest,
                "audit": audit,
                "verification_evidence": evidence,
                "frozen_release_binding": binding,
                "audit_evidence": audit_evidence,
            }
            values.update(update)
            record_set = PublicationRecordSet(**values)
            with pytest.raises(PublicationIntegrityError) as excinfo:
                validate_publication_integrity(record_set)
            assert excinfo.value.code in (
                "publication_audit_evidence_mismatch",
                # Without the manifest, an earlier integrity rule fires
                # first; either way the record set fails closed.
                "verification_manifest_mismatch",
            )
            assert publication_audit_evidence_classification(record_set) == "complete"
        assert publication_audit_evidence_classification(
            PublicationRecordSet(
                bundle_id=bundle.bundle_id,
                bundle_fingerprint=bundle.fingerprint,
            )
        ) == "incomplete"
