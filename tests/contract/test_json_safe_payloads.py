"""Regression tests: fingerprint-critical domain payloads are JSON-safe.

Every identity-critical domain (Semantic Query IR, semantic Bundle,
accepted-assertion manifest, Verification Suite plan/evidence, assembly
audit evidence, catalog envelope payloads, workflow artifact identity) must
prepare its canonical payload into strict JSON-safe values before
canonicalization, and the strict ``jcs-v1`` serialization must match the
legacy serialization byte-for-byte for these safe payloads so existing
golden vectors and persisted fingerprints never drift.
"""

from __future__ import annotations

import json
from typing import Any

from nl2data_core.canonical import (
    CANONICALIZATION_PROFILE_JCS,
    canonical_json,
    sha256_fingerprint,
    strict_canonical_json,
    strict_sha256_fingerprint,
    validate_json_safe,
)
from nl2data_core.planning.ir.fixtures import (
    GOLDEN_CANONICAL_JSON,
    GOLDEN_FINGERPRINT,
    golden_ir,
)


def _assert_json_safe_and_undrifted(payload: Any) -> None:
    """The payload validates under the strict profile and does not drift."""
    validate_json_safe(payload)
    assert strict_canonical_json(payload) == canonical_json(payload)
    assert strict_sha256_fingerprint(payload) == sha256_fingerprint(payload)


def _assert_json_safe(payload: Any) -> None:
    """The payload validates under the strict profile.

    Used for document-persisted payloads (durable workflow snapshots)
    whose float members render differently under JCS number formatting
    but which are never identity-pinned under the legacy profile.
    """
    validate_json_safe(payload)
    strict = strict_canonical_json(payload)
    assert strict == strict_canonical_json(json.loads(strict))


class TestSemanticQueryIRPayloads:
    def test_golden_ir_canonical_payload_is_json_safe(self) -> None:
        ir = golden_ir()
        _assert_json_safe_and_undrifted(ir.canonical_payload())

    def test_golden_ir_strict_serialization_matches_frozen_vectors(self) -> None:
        ir = golden_ir()
        assert ir.serialize_canonical() == GOLDEN_CANONICAL_JSON
        assert ir.fingerprint == GOLDEN_FINGERPRINT
        assert strict_canonical_json(ir.canonical_payload()) == GOLDEN_CANONICAL_JSON
        assert strict_sha256_fingerprint(ir.canonical_payload()) == GOLDEN_FINGERPRINT


class TestBundlePayloads:
    def test_bundle_file_payload_is_json_safe(self) -> None:
        bundle = _bundle()
        _assert_json_safe_and_undrifted(bundle.file_payload())
        _assert_json_safe_and_undrifted(bundle.canonical_payload())

    def test_bundle_serialize_canonical_is_strict_safe(self) -> None:
        bundle = _bundle()
        assert bundle.serialize_canonical() == strict_canonical_json(
            bundle.file_payload()
        )
        assert bundle.serialize_canonical() == canonical_json(bundle.file_payload())


class TestVerificationPayloads:
    def test_verification_plan_payload_is_json_safe(self) -> None:
        from nl2data_core.verification.models import (
            OutcomeAssertion,
            SmokeQueryCase,
            VerificationPlan,
        )

        plan = VerificationPlan(
            policy_profile="compatibility",
            smoke_cases=(
                SmokeQueryCase(
                    case_id="smoke-1",
                    query=golden_ir(),
                    fixture_profile_id="fixture-1",
                    assertions=(
                        OutcomeAssertion(assertion_id="a1", expected="success"),
                    ),
                ),
            ),
        )
        _assert_json_safe_and_undrifted(plan.canonical_payload())
        assert plan.serialize_canonical() == strict_canonical_json(
            plan.canonical_payload()
        )


class TestAssemblyAuditEvidencePayloads:
    def test_audit_entry_canonical_payload_is_json_safe(self) -> None:
        from nl2data_core.assembly.audit_evidence import (
            AssemblyAuditEvidenceEntry,
            AuditEventKind,
            AuditOutcome,
            AuditSubjectKind,
        )

        entry = AssemblyAuditEvidenceEntry(
            event_id="activate-1",
            event_kind=AuditEventKind.ACTIVATION,
            subject_kind=AuditSubjectKind.ACTIVATION,
            subject_reference="publish-1",
            tenant_scope_fingerprint="sha256:" + "1a" * 32,
            source_scope_fingerprint="sha256:" + "1b" * 32,
            bundle_fingerprint="sha256:" + "2a" * 32,
            lifecycle_reference="publish-1",
            outcome=AuditOutcome.SUCCEEDED,
        )
        _assert_json_safe_and_undrifted(entry.canonical_payload())
        assert entry.verify_fingerprint() is True
        assert entry.fingerprint == strict_sha256_fingerprint(entry.canonical_payload())


class TestWorkflowPayloads:
    def test_artifact_fingerprint_payload_is_json_safe(self) -> None:
        artifact = {"collection": "orders", "pipeline": [{"$match": {"region": "north"}}]}
        payload: dict[str, Any] = {
            "artifact": artifact,
            "adapter_type": "mongodb",
        }
        _assert_json_safe_and_undrifted(payload)

    def test_compatibility_and_gate_evidence_payloads_are_json_safe(self) -> None:
        ir = golden_ir()
        compatibility = {"ir_version": ir.ir_version, "ir_fingerprint": ir.fingerprint}
        gate_evidence = {
            "ir_fingerprint": ir.fingerprint,
            "policy_fingerprint": "sha256:" + "ab" * 32,
        }
        _assert_json_safe_and_undrifted(compatibility)
        _assert_json_safe_and_undrifted(gate_evidence)


class TestCatalogEnvelopePayloads:
    def test_envelope_payload_is_json_safe_and_profile_bound(self) -> None:
        from nl2data_semantic_catalog_postgres.envelope import (
            ArtifactKind,
            decode_envelope,
            encode_envelope,
        )

        payload: dict[str, Any] = {"snapshot_id": "snap-1", "objects": []}
        _assert_json_safe_and_undrifted(payload)
        text = encode_envelope(
            ArtifactKind.SNAPSHOT,
            payload,
            strict_sha256_fingerprint(payload),
            max_envelope_bytes=1_048_576,
            max_payload_bytes=1_048_576,
        )
        envelope = decode_envelope(
            text,
            expected_kind=ArtifactKind.SNAPSHOT,
            supported_schema_version=1,
            max_envelope_bytes=1_048_576,
            max_payload_bytes=1_048_576,
        )
        assert envelope.fingerprint == strict_sha256_fingerprint(payload)
        assert envelope.canonicalization_profile == CANONICALIZATION_PROFILE_JCS


class TestWorkflowSnapshotPayloads:
    def test_durable_snapshot_safe_state_is_json_safe(self) -> None:
        from datetime import UTC, datetime

        from nl2data_core.workflow.durable import serialize_snapshot
        from nl2data_core.workflow.models import (
            WorkflowEvent,
            WorkflowState,
            WorkflowStatus,
        )

        state = WorkflowState(
            workflow_id="wf-1",
            request_id="req-1",
            status=WorkflowStatus.RUNNING,
            attempts=1,
            events=(
                WorkflowEvent(
                    event_id="ev-1",
                    workflow_id="wf-1",
                    from_status=WorkflowStatus.CREATED,
                    to_status=WorkflowStatus.RUNNING,
                    occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
                ),
            ),
            evidence_fingerprints=frozenset({"sha256:" + "e" * 64}),
        )
        document = json.loads(serialize_snapshot(state))
        _assert_json_safe(document["state"])


def _bundle():
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
    return SemanticModelBundle(
        bundle_id="sales_model",
        model_version="1.0.0",
        descriptor=descriptor,
        sources=(SemanticSourceReference(reference_id="sales", source_id="sales"),),
        provenance=BundleProvenance(
            owner_reference="team-analytics", quality=BundleQualityStatus.APPROVED
        ),
    )
