"""Characterization tests for semantic control-plane public contracts."""

from __future__ import annotations

import hashlib
import inspect
import json
from datetime import UTC, datetime, timedelta

from nl2data_admin_service.schema import build_schema
from nl2data_admin_service.service import AdminService
from nl2data_core import verification
from nl2data_core.assembly import (
    ASSEMBLY_API_VERSION,
    AssemblyDraft,
    AssemblyState,
    AssertionProvenance,
    AssertionType,
    LifecycleAuthorizationContext,
    LifecycleAuthorizationDecision,
    LifecycleAuthorizationRequest,
    LifecycleRole,
    ReviewState,
    SemanticAssertion,
    SeparationOfDutiesDecision,
    SeparationOfDutiesMode,
)
from nl2data_core.assembly.publishing import (
    AssemblyPublishIssue,
    ManifestBundleVerification,
    publish_assembly,
)
from nl2data_core.bundles import (
    BundleProvenance,
    BundleQualityStatus,
    InMemorySemanticBundleCatalog,
    SemanticModelBundle,
    SemanticSourceReference,
)
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.governance.models import PolicyScope
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.verification import COMPATIBILITY_POLICY, VerificationPlan
from nl2data_core.verification.execution import VerificationExecutionContext
from nl2data_core.verification.structural import CoreStructuralVerificationRunner
from nl2data_core.verification.suite import compatibility_suite_evidence
from nl2data_core.views import (
    SemanticDescriptor,
    SemanticEntityDescriptor,
    SemanticFieldDescriptor,
)

TENANT_SCOPE = "sha256:" + "a" * 64


class Authorizer:
    def authorize(
        self,
        request: LifecycleAuthorizationRequest,
    ) -> LifecycleAuthorizationDecision:
        return LifecycleAuthorizationDecision(allowed=True)


class Verifier:
    def __init__(self, *, valid: bool = True, raises: bool = False) -> None:
        self.valid = valid
        self.raises = raises

    def verify(
        self,
        draft: AssemblyDraft,
        manifest: object,
        bundle: SemanticModelBundle,
    ) -> ManifestBundleVerification:
        if self.raises:
            raise RuntimeError("backend detail must not escape")
        issues = ()
        if not self.valid:
            issues = (
                AssemblyPublishIssue(
                    code="semantic_mismatch",
                    message="manifest does not match emitted semantic content",
                ),
            )
        return ManifestBundleVerification(valid=self.valid, issues=issues)


class Emitter:
    def __init__(self, *, raises: bool = False) -> None:
        self.raises = raises

    def emit(self, draft: AssemblyDraft) -> SemanticModelBundle:
        if self.raises:
            raise RuntimeError("emitter detail must not escape")
        return _bundle()


def _bundle() -> SemanticModelBundle:
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
                        field_id="amount",
                        label="Amount",
                        data_type="float",
                    ),
                ),
            ),
        ),
    )
    return SemanticModelBundle(
        bundle_id="sales",
        model_version="1.0.0",
        descriptor=descriptor,
        sources=(SemanticSourceReference(reference_id="sales", source_id="sales"),),
        provenance=BundleProvenance(
            owner_reference="team-analytics",
            quality=BundleQualityStatus.APPROVED,
        ),
    )


def _assertion(*, reviewed: bool = True) -> SemanticAssertion:
    value = SemanticAssertion.create(
        type=AssertionType.ENTITY,
        payload={
            "descriptor_id": "sales",
            "entity_id": "orders",
            "label": "Orders",
        },
        provenance=AssertionProvenance(kind="manual"),
    )
    if not reviewed:
        return value
    return value.bind_review(
        state=ReviewState.APPROVED,
        reviewer_reference="reviewer-1",
    )


def _draft(
    *, reviewed: bool = True, verification_plan: VerificationPlan | None = None
) -> AssemblyDraft:
    return AssemblyDraft(
        apiVersion=ASSEMBLY_API_VERSION,
        draft_id="draft-1",
        bundle_id="sales",
        source_id="sales",
        model_version="1.0.0",
        state=AssemblyState.APPROVED,
        draft_revision=3,
        assertions=(_assertion(reviewed=reviewed),),
        author_reference="author-1",
        verification_plan=verification_plan,
        approved_verification_plan_fingerprint=(
            verification_plan.fingerprint if verification_plan is not None else None
        ),
    )


def _authorization() -> LifecycleAuthorizationContext:
    return LifecycleAuthorizationContext(
        operator_reference="publisher-1",
        tenant_scope_fingerprint=TENANT_SCOPE,
        source_id="sales",
        roles=frozenset({LifecycleRole.PUBLISHER}),
    )


def _separation(*, allowed: bool = True) -> SeparationOfDutiesDecision:
    return SeparationOfDutiesDecision(
        allowed=allowed,
        mode=SeparationOfDutiesMode.STRICT,
        reason_code="authorized" if allowed else "role_overlap",
    )


def _publish(
    catalog: InMemorySemanticBundleCatalog,
    *,
    draft_value: AssemblyDraft | None = None,
    verifier: Verifier | None = None,
    emitter: Emitter | None = None,
    separation_value: SeparationOfDutiesDecision | None = None,
):
    return publish_assembly(
        draft_value or _draft(),
        expected_revision=3,
        authorization=_authorization(),
        authorizer=Authorizer(),
        separation=separation_value or _separation(),
        emitter=emitter or Emitter(),
        verifier=verifier or Verifier(),
        catalog=catalog,
    )


def _stable_json_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _without_clock_fields(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _without_clock_fields(item)
            for key, item in value.items()
            if key not in {"created_at", "published_at", "reviewed_at"}
        }
    if isinstance(value, list):
        return [_without_clock_fields(item) for item in value]
    return value


def test_verification_public_exports_are_stable() -> None:
    assert list(verification.__all__) == [
        "AggregateTotalContract",
        "BUILTIN_POLICIES",
        "CapabilityRequirements",
        "COMPATIBILITY_POLICY",
        "ErrorCodeAssertion",
        "ExactProtectedResultContract",
        "ExpectedScalarKind",
        "IsNullAssertion",
        "MappingOutcomeContract",
        "NullBehaviorContract",
        "OutcomeAssertion",
        "PRODUCTION_POLICY",
        "ResultShapeAssertion",
        "RowCountAssertion",
        "RowCountEqualityContract",
        "RowCountRangeContract",
        "ScalarEqualityContract",
        "ScalarEqualsAssertion",
        "SemanticContractCase",
        "SmokeQueryCase",
        "StructuredErrorCodeContract",
        "TaggedExpectedScalar",
        "VerificationCaseEvidence",
        "VerificationDeadlines",
        "VerificationLayer",
        "VerificationLayerEvidence",
        "VerificationPlan",
        "VerificationPolicy",
        "VerificationStatus",
        "VerificationSuiteEvidence",
        "validate_stricter_policy",
    ]
    assert verification.VerificationPlan.__module__ == "nl2data_core.verification.models"
    assert verification.VerificationSuiteEvidence.__module__ == (
        "nl2data_core.verification.models"
    )


def test_admin_public_method_signatures_are_stable() -> None:
    public_methods = {
        name: str(inspect.signature(getattr(AdminService, name)))
        for name in dir(AdminService)
        if not name.startswith("_") and callable(getattr(AdminService, name))
    }
    assert public_methods == {
        "activate_published_fingerprint": "(self, bundle_id: 'str', fingerprint: 'str', *, auth_context: 'AuthContext') -> 'BundleLifecycleResult'",
        "approve_assembly_draft": "(self, draft_id: 'str', command: 'DraftRevisionCommand', *, auth_context: 'AuthContext') -> 'DraftMutationResult'",
        "cancel_job": "(self, job_id: 'str', *, auth_context: 'AuthContext') -> 'JobInfo'",
        "capabilities": "(self) -> 'CapabilitiesResult'",
        "create_draft": "(self, draft: 'AssemblyDraft', *, auth_context: 'AuthContext') -> 'DraftMutationResult'",
        "create_draft_from_proposals": "(self, snapshot_fingerprint: 'str', *, descriptor_id: 'str', draft_id: 'str', bundle_id: 'str', model_version: 'str', auth_context: 'AuthContext') -> 'DraftMutationResult'",
        "decide_draft_assertion": "(self, draft_id: 'str', command: 'AssertionDecisionCommand', *, auth_context: 'AuthContext') -> 'DraftMutationResult'",
        "edit_draft": "(self, draft_id: 'str', *, expected_revision: 'int', assertions: 'tuple[SemanticAssertion, ...] | None' = None, deployment_bindings: 'tuple[DeploymentBinding, ...] | None' = None, auth_context: 'AuthContext') -> 'DraftMutationResult'",
        "get_active_snapshot": "(self, source_id: 'str', *, auth_context: 'AuthContext') -> 'SnapshotDetail'",
        "get_bundle": "(self, bundle_id: 'str', version: 'str', *, auth_context: 'AuthContext') -> 'BundleDetail'",
        "get_draft": "(self, draft_id: 'str', *, auth_context: 'AuthContext') -> 'AssemblyDraftDetail'",
        "get_drift_status": "(self, snapshot_fingerprint: 'str', *, auth_context: 'AuthContext') -> 'DriftStatus'",
        "get_job": "(self, job_id: 'str', *, auth_context: 'AuthContext') -> 'JobInfo'",
        "get_proposal_set": "(self, snapshot_fingerprint: 'str', *, auth_context: 'AuthContext') -> 'ProposalSetDetail'",
        "get_publish_audit": "(self, bundle_id: 'str', fingerprint: 'str', *, auth_context: 'AuthContext') -> 'PublishAuditSummary'",
        "get_snapshot": "(self, snapshot_fingerprint: 'str', *, auth_context: 'AuthContext') -> 'SnapshotDetail'",
        "get_verification_evidence": "(self, bundle_id: 'str', fingerprint: 'str', *, auth_context: 'AuthContext') -> 'VerificationEvidenceReference'",
        "import_authoring": "(self, command: 'ImportAuthoringCommand', *, auth_context: 'AuthContext') -> 'AuthoringImportResult'",
        "lifecycle_command": "(self, command: 'BundleLifecycleCommand', *, auth_context: 'AuthContext') -> 'BundleLifecycleResult'",
        "lint_authoring": "(self, command: 'LintAuthoringCommand', *, auth_context: 'AuthContext') -> 'LintResultDetail'",
        "lint_draft": "(self, draft_id: 'str', command: 'LintDraftCommand', *, auth_context: 'AuthContext') -> 'LintResultDetail'",
        "list_bundles": "(self, bundle_id: 'str', *, auth_context: 'AuthContext', pagination: 'PaginationParams | None' = None) -> 'PaginatedResult'",
        "list_published_versions": "(self, bundle_id: 'str', *, auth_context: 'AuthContext') -> 'VersionListResult'",
        "list_snapshots": "(self, *, auth_context: 'AuthContext', pagination: 'PaginationParams | None' = None) -> 'PaginatedResult'",
        "publish_bundle": "(self, bundle: 'SemanticModelBundle', *, auth_context: 'AuthContext', idempotency_key: 'str') -> 'BundleLifecycleResult'",
        "publish_draft": "(self, draft_id: 'str', command: 'DraftRevisionCommand | PublishDraftCommand', *, auth_context: 'AuthContext') -> 'PublishAssemblyResult'",
        "review_proposals": "(self, snapshot_fingerprint: 'str', command: 'ReviewCommand', *, auth_context: 'AuthContext') -> 'ReviewResult'",
        "rollback_published_fingerprint": "(self, bundle_id: 'str', fingerprint: 'str', *, auth_context: 'AuthContext') -> 'BundleLifecycleResult'",
        "submit_discovery": "(self, source_id: 'str', *, auth_context: 'AuthContext', idempotency_key: 'str') -> 'JobInfo'",
        "submit_draft_for_review": "(self, draft_id: 'str', command: 'DraftRevisionCommand', *, auth_context: 'AuthContext') -> 'DraftMutationResult'",
        "validate_authoring": "(self, command: 'AuthoringDocumentCommand', *, auth_context: 'AuthContext') -> 'AuthoringValidationResult'",
        "validate_bundle": "(self, bundle: 'SemanticModelBundle', *, auth_context: 'AuthContext') -> 'BundleValidationResult'",
        "verify_draft": "(self, draft_id: 'str', command: 'VerifyDraftCommand', *, auth_context: 'AuthContext') -> 'DraftVerificationResult'",
    }


def test_admin_generated_dto_schema_is_stable() -> None:
    schema = build_schema("v1")
    assert sorted(schema.commands) == [
        "AssertionDecisionCommand",
        "AuthoringDocumentCommand",
        "BundleLifecycleCommand",
        "DraftRevisionCommand",
        "ImportAuthoringCommand",
        "LintAuthoringCommand",
        "LintDraftCommand",
        "PaginationParams",
        "PublishDraftCommand",
        "ReviewCommand",
        "VerifyDraftCommand",
    ]
    assert sorted(schema.results) == [
        "AdminLintDiagnostic",
        "AdminResult",
        "AssemblyAssertionSummary",
        "AssemblyDraftDetail",
        "AssemblyDraftSummary",
        "AuthoringDiagnosticDetail",
        "AuthoringImportResult",
        "AuthoringSemanticSummary",
        "AuthoringValidationResult",
        "BundleDetail",
        "BundleLifecycleResult",
        "BundleListItem",
        "BundleValidationResult",
        "CapabilitiesResult",
        "DeploymentBindingSummary",
        "DraftMutationResult",
        "DraftVerificationResult",
        "DriftStatus",
        "ErrorDetail",
        "JobInfo",
        "LintResultDetail",
        "PaginatedResult",
        "ProposalListItem",
        "ProposalSetDetail",
        "PublishAssemblyResult",
        "PublishAuditSummary",
        "PublishedVersionItem",
        "ReviewResult",
        "SnapshotDetail",
        "SnapshotListItem",
        "VerificationCaseSummary",
        "VerificationEvidenceReference",
        "VerificationLayerSummary",
        "VersionListResult",
    ]
    model_schemas = {
        name: model.model_json_schema(mode="validation")
        for name, model in {**schema.commands, **schema.results}.items()
    }
    assert _stable_json_hash(model_schemas) == (
        "5dc90b4cde3c10f3b233ef920703b01693b17dabd726983a3cab0acaf252e545"
    )


def test_publication_wire_payloads_and_fingerprints_are_stable() -> None:
    catalog = InMemorySemanticBundleCatalog()
    outcome = _publish(catalog)
    assert outcome.bundle is not None
    assert outcome.manifest is not None
    audit = catalog.publish_audit(
        "sales",
        outcome.bundle.fingerprint,
        tenant_scope_fingerprint=TENANT_SCOPE,
    )
    evidence = catalog.verification_evidence(
        "sales",
        outcome.bundle.fingerprint,
        tenant_scope_fingerprint=TENANT_SCOPE,
    )
    assert audit is not None
    assert evidence is not None

    audit_payload = audit.safe_payload()
    payload = {
        "draft": _without_clock_fields(_draft().file_payload()),
        "bundle": _without_clock_fields(outcome.bundle.file_payload()),
        "manifest": outcome.manifest.canonical_payload(),
        "audit": _without_clock_fields(audit_payload),
        "evidence": evidence.evidence_payload(),
        "outcome": {
            "kind": outcome.kind,
            "audit_reference": outcome.audit_reference,
            "verification_evidence_reference": outcome.verification_evidence_reference,
            "idempotency_status": outcome.idempotency_status.value,
        },
    }
    assert outcome.bundle.fingerprint == (
        "sha256:0f74c63939cc856ea45d62de9dd1232ef3a9a2bdc37d757fc8c01dba0a87f932"
    )
    assert sha256_fingerprint(outcome.manifest.canonical_payload()) == (
        "sha256:9608237a6041ba3e753fad1390689aaafa83cada054c89e2939a819de68f1b14"
    )
    assert evidence.fingerprint == (
        "sha256:0c805aa35ebd8ffd182052095a462acd186ac9ae0b0d246906f4a01843f63ae3"
    )
    assert audit.verification.release_binding_fingerprint == (
        "sha256:6363b45db56d364360a23503d2c952f5c008923e8f1b15e09280081e11283464"
    )
    assert audit.audit_id == "publish-943e7512a41afa9c60de465d"
    assert _stable_json_hash(payload) == (
        "858dc7663cf539ff3b9297d71443428dd83558368fd7e70297c1df68daa1e7e2"
    )


def test_publication_rejection_codes_are_stable_and_safe() -> None:
    plan = VerificationPlan(policy_profile="compatibility-v1")
    approved = _draft(verification_plan=plan)
    candidate = _bundle()
    structural = CoreStructuralVerificationRunner().run(
        approved,
        candidate,
        expected_revision=approved.draft_revision,
        expected_source_id="sales",
    )
    assert structural.manifest is not None
    context = VerificationExecutionContext(
        candidate=candidate,
        manifest=structural.manifest,
        view=AuthorizedView(
            source_id="sales",
            root_entity_ids=frozenset({"orders"}),
            field_ids=frozenset({"amount"}),
        ),
        policy=COMPATIBILITY_POLICY,
        policy_scope=PolicyScope(
            policy_id="publish-verification",
            source_ids=frozenset({"sales"}),
            resource_ids=frozenset({"orders"}),
            operation_ids=frozenset({"select"}),
            field_ids=frozenset({"amount"}),
        ),
        tenant_scope_fingerprint=TENANT_SCOPE,
        source_scope_fingerprint=sha256_fingerprint({"source_id": "sales"}),
        deadline_at=datetime.now(UTC) + timedelta(seconds=10),
    )
    base = compatibility_suite_evidence(
        structural_evidence=structural.evidence,
        draft_id=approved.draft_id,
        draft_revision=approved.draft_revision,
        bundle_fingerprint=candidate.fingerprint,
        manifest_fingerprint=sha256_fingerprint(structural.manifest.canonical_payload()),
        tenant_scope_fingerprint=TENANT_SCOPE,
        source_scope_fingerprint=context.source_scope_fingerprint,
    )
    stale_evidence = type(base).model_validate(
        {**base.model_dump(), "draft_revision": 2, "plan_fingerprint": plan.fingerprint}
    )
    outcomes = {
        "pending": _publish(
            InMemorySemanticBundleCatalog(),
            draft_value=_draft(reviewed=False),
        ),
        "separation": _publish(
            InMemorySemanticBundleCatalog(),
            separation_value=_separation(allowed=False),
        ),
        "emitter": _publish(InMemorySemanticBundleCatalog(), emitter=Emitter(raises=True)),
        "verifier": _publish(InMemorySemanticBundleCatalog(), verifier=Verifier(raises=True)),
        "missing_evidence": publish_assembly(
            approved,
            expected_revision=3,
            authorization=_authorization(),
            authorizer=Authorizer(),
            separation=_separation(),
            emitter=Emitter(),
            verifier=Verifier(),
            catalog=InMemorySemanticBundleCatalog(),
            verification_policy=COMPATIBILITY_POLICY,
            verification_context=context,
        ),
        "stale_evidence": publish_assembly(
            approved,
            expected_revision=3,
            authorization=_authorization(),
            authorizer=Authorizer(),
            separation=_separation(),
            emitter=Emitter(),
            verifier=Verifier(),
            catalog=InMemorySemanticBundleCatalog(),
            verification_policy=COMPATIBILITY_POLICY,
            verification_context=context,
            verification_evidence=stale_evidence,
        ),
    }
    assert {name: outcome.issues[0].code for name, outcome in outcomes.items()} == {
        "pending": "pending_assertions",
        "separation": "separation_of_duties_failed",
        "emitter": "bundle_emission_failed",
        "verifier": "verification_failed",
        "missing_evidence": "verification_evidence_required",
        "stale_evidence": "verification_evidence_mismatch",
    }