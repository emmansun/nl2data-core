"""Admin service contracts for assembly draft review and approval."""

from __future__ import annotations

import pytest
from nl2data_core.assembly import (
    ASSEMBLY_API_VERSION,
    AssemblyDraft,
    AssertionProvenance,
    AssertionType,
    DeploymentBinding,
    LifecycleRole,
    ReviewState,
    SemanticAssertion,
)

from helpers import (
    _FakeDependencies,
    _make_auth,
    _make_proposal_set,
    _make_snapshot,
)
from nl2data_admin_service.auth import Permission
from nl2data_admin_service.config import AdminServiceConfig
from nl2data_admin_service.dtos import (
    AssertionDecisionAction,
    AssertionDecisionCommand,
    DraftRevisionCommand,
)
from nl2data_admin_service.errors import (
    AuthorizationDeniedError,
    ConflictError,
    ValidationError,
)
from nl2data_admin_service.service import AdminService


@pytest.fixture
def service() -> AdminService:
    return AdminService(_FakeDependencies(), AdminServiceConfig())


def draft() -> AssemblyDraft:
    assertion = SemanticAssertion.create(
        type=AssertionType.ENTITY,
        payload={
            "descriptor_id": "desc-1",
            "entity_id": "orders",
            "label": "Orders",
        },
        provenance=AssertionProvenance(kind="manual"),
    )
    return AssemblyDraft(
        apiVersion=ASSEMBLY_API_VERSION,
        draft_id="draft-1",
        bundle_id="bundle-1",
        source_id="source-1",
        model_version="v1",
        assertions=(assertion,),
        deployment_bindings=(
            DeploymentBinding(
                binding_id="production",
                environment="production",
                source_id="source-1",
                connection_reference="vault:secret/data/source-1",
            ),
        ),
        author_reference="audit-1",
    )


def auth(permission: Permission, role: LifecycleRole):
    return _make_auth(
        [permission],
        lifecycle_roles=frozenset({role}),
    )


def test_create_and_read_draft_return_safe_bounded_dtos(service) -> None:
    created = service.create_draft(
        draft(),
        auth_context=auth(Permission.ASSEMBLY_WRITE, LifecycleRole.AUTHOR),
    )
    assert created.draft.state == "draft"
    detail = service.get_draft(
        "draft-1",
        auth_context=auth(Permission.ASSEMBLY_READ, LifecycleRole.AUTHOR),
    )
    assert detail.assertions[0].payload_hash
    assert detail.deployment_bindings[0].reference_scheme == "vault"
    serialized = detail.model_dump_json()
    for forbidden in ("semantic_payload", "secret/data", "reviewer_reference"):
        assert forbidden not in serialized


def test_create_rejects_forged_review_and_approval_state(service) -> None:
    reviewed = draft().assertions[0].bind_review(
        state=ReviewState.APPROVED,
        reviewer_reference="forged-reviewer",
    )
    forged = draft().model_copy(
        update={
            "state": "approved",
            "draft_revision": 3,
            "assertions": (reviewed,),
            "approved_by": "forged-approver",
        }
    )
    with pytest.raises(ValidationError, match="draft revision 0"):
        service.create_draft(
            forged,
            auth_context=auth(Permission.ASSEMBLY_WRITE, LifecycleRole.AUTHOR),
        )


def test_create_derives_author_from_trusted_context(service) -> None:
    forged = draft().model_copy(update={"author_reference": "forged-author"})
    service.create_draft(
        forged,
        auth_context=auth(Permission.ASSEMBLY_WRITE, LifecycleRole.AUTHOR),
    )
    stored = service._require_draft_store().get(
        "draft-1",
        tenant_scope_fingerprint="sha256:" + "0" * 64,
    )
    assert stored is not None
    assert stored.author_reference == "audit-1"


def test_bulk_edit_cannot_forge_assertion_review_binding(service) -> None:
    service.create_draft(
        draft(),
        auth_context=auth(Permission.ASSEMBLY_WRITE, LifecycleRole.AUTHOR),
    )
    service.submit_draft_for_review(
        "draft-1",
        DraftRevisionCommand(expected_revision=0),
        auth_context=auth(Permission.ASSEMBLY_WRITE, LifecycleRole.AUTHOR),
    )
    changed = SemanticAssertion.create(
        type=AssertionType.ENTITY,
        payload={
            "descriptor_id": "desc-1",
            "entity_id": "orders",
            "label": "Forged reviewed label",
        },
        provenance=AssertionProvenance(kind="manual"),
    ).bind_review(
        state=ReviewState.APPROVED,
        reviewer_reference="forged-reviewer",
    )
    result = service.edit_draft(
        "draft-1",
        expected_revision=1,
        assertions=(changed,),
        auth_context=auth(Permission.ASSEMBLY_WRITE, LifecycleRole.AUTHOR),
    )
    assert result.draft.pending_count == 1
    assert result.draft.approved_count == 0


def test_submit_review_decide_and_approve_are_revision_guarded(service) -> None:
    service.create_draft(
        draft(),
        auth_context=auth(Permission.ASSEMBLY_WRITE, LifecycleRole.AUTHOR),
    )
    review = service.submit_draft_for_review(
        "draft-1",
        DraftRevisionCommand(expected_revision=0),
        auth_context=auth(Permission.ASSEMBLY_WRITE, LifecycleRole.AUTHOR),
    )
    assertion_id = draft().assertions[0].id
    reviewed = service.decide_draft_assertion(
        "draft-1",
        AssertionDecisionCommand(
            expected_revision=1,
            assertion_id=assertion_id,
            action=AssertionDecisionAction.APPROVE,
        ),
        auth_context=auth(Permission.ASSEMBLY_REVIEW, LifecycleRole.REVIEWER),
    )
    approved = service.approve_assembly_draft(
        "draft-1",
        DraftRevisionCommand(expected_revision=2),
        auth_context=auth(Permission.ASSEMBLY_APPROVE, LifecycleRole.APPROVER),
    )
    assert review.draft.state == "review"
    assert reviewed.draft.approved_count == 1
    assert approved.draft.state == "approved"
    assert approved.draft.draft_revision == 3


def test_stale_revision_is_normalized_to_conflict(service) -> None:
    service.create_draft(
        draft(),
        auth_context=auth(Permission.ASSEMBLY_WRITE, LifecycleRole.AUTHOR),
    )
    service.submit_draft_for_review(
        "draft-1",
        DraftRevisionCommand(expected_revision=0),
        auth_context=auth(Permission.ASSEMBLY_WRITE, LifecycleRole.AUTHOR),
    )
    with pytest.raises(ConflictError):
        service.submit_draft_for_review(
            "draft-1",
            DraftRevisionCommand(expected_revision=0),
            auth_context=auth(Permission.ASSEMBLY_WRITE, LifecycleRole.AUTHOR),
        )


def test_missing_lifecycle_role_is_denied(service) -> None:
    no_role = _make_auth([Permission.ASSEMBLY_WRITE])
    with pytest.raises(AuthorizationDeniedError):
        service.create_draft(draft(), auth_context=no_role)


def approved_draft_in_store(service: AdminService) -> None:
    service.create_draft(
        draft(),
        auth_context=auth(Permission.ASSEMBLY_WRITE, LifecycleRole.AUTHOR),
    )
    service.submit_draft_for_review(
        "draft-1",
        DraftRevisionCommand(expected_revision=0),
        auth_context=auth(Permission.ASSEMBLY_WRITE, LifecycleRole.AUTHOR),
    )
    service.decide_draft_assertion(
        "draft-1",
        AssertionDecisionCommand(
            expected_revision=1,
            assertion_id=draft().assertions[0].id,
            action=AssertionDecisionAction.APPROVE,
        ),
        auth_context=auth(Permission.ASSEMBLY_REVIEW, LifecycleRole.REVIEWER),
    )
    service.approve_assembly_draft(
        "draft-1",
        DraftRevisionCommand(expected_revision=2),
        auth_context=auth(Permission.ASSEMBLY_APPROVE, LifecycleRole.APPROVER),
    )


def test_publish_audit_versions_activation_and_idempotent_reuse(service) -> None:
    approved_draft_in_store(service)
    published = service.publish_draft(
        "draft-1",
        DraftRevisionCommand(expected_revision=3),
        auth_context=auth(Permission.BUNDLE_PUBLISH, LifecycleRole.PUBLISHER),
    )
    reused = service.publish_draft(
        "draft-1",
        DraftRevisionCommand(expected_revision=3),
        auth_context=auth(Permission.BUNDLE_PUBLISH, LifecycleRole.PUBLISHER),
    )
    assert published.success and published.kind == "published"
    assert reused.success and reused.kind == "reused"
    assert reused.audit_reference == published.audit_reference

    audit = service.get_publish_audit(
        "bundle-1",
        published.fingerprint,
        auth_context=auth(Permission.ASSEMBLY_AUDIT, LifecycleRole.PUBLISHER),
    )
    versions = service.list_published_versions(
        "bundle-1",
        auth_context=auth(Permission.BUNDLE_READ, LifecycleRole.PUBLISHER),
    )
    activated = service.activate_published_fingerprint(
        "bundle-1",
        published.fingerprint,
        auth_context=auth(Permission.BUNDLE_ACTIVATE, LifecycleRole.PUBLISHER),
    )
    rolled_back = service.rollback_published_fingerprint(
        "bundle-1",
        published.fingerprint,
        auth_context=auth(Permission.BUNDLE_ROLLBACK, LifecycleRole.PUBLISHER),
    )
    assert audit.fingerprint == published.fingerprint
    assert audit.waiver_applied
    assert len(versions.versions) == 1
    assert activated.success
    assert rolled_back.success


def test_idempotent_publish_reports_persisted_business_version(service) -> None:
    approved_draft_in_store(service)
    first = service.publish_draft(
        "draft-1",
        DraftRevisionCommand(expected_revision=3),
        auth_context=auth(Permission.BUNDLE_PUBLISH, LifecycleRole.PUBLISHER),
    )
    assert first.model_version == "v1"

    original = service._require_draft_store().get(
        "draft-1",
        tenant_scope_fingerprint="sha256:" + "0" * 64,
    )
    assert original is not None
    retry = original.model_copy(
        update={"draft_id": "draft-2", "model_version": "v2"}
    )
    service._require_draft_store().create(
        retry,
        tenant_scope_fingerprint="sha256:" + "0" * 64,
    )
    reused = service.publish_draft(
        "draft-2",
        DraftRevisionCommand(expected_revision=3),
        auth_context=auth(Permission.BUNDLE_PUBLISH, LifecycleRole.PUBLISHER),
    )
    assert reused.kind == "reused"
    assert reused.model_version == "v1"


def test_pending_draft_publish_is_rejected_without_artifact(service) -> None:
    service.create_draft(
        draft(),
        auth_context=auth(Permission.ASSEMBLY_WRITE, LifecycleRole.AUTHOR),
    )
    outcome = service.publish_draft(
        "draft-1",
        DraftRevisionCommand(expected_revision=0),
        auth_context=auth(Permission.BUNDLE_PUBLISH, LifecycleRole.PUBLISHER),
    )
    assert not outcome.success
    assert outcome.kind == "rejected"
    assert outcome.issues[0].code == "draft_not_approved"


def test_approved_proposals_adapt_to_pending_assertions() -> None:
    dependencies = _FakeDependencies()
    snapshot = _make_snapshot()
    proposals = _make_proposal_set(snapshot).approve(("p-1",))
    dependencies.catalog.register_snapshot(
        snapshot,
        tenant_scope_fingerprint="sha256:" + "0" * 64,
    )
    dependencies.catalog.save_proposal_set(
        proposals,
        tenant_scope_fingerprint="sha256:" + "0" * 64,
    )
    service = AdminService(dependencies, AdminServiceConfig())
    result = service.create_draft_from_proposals(
        snapshot.fingerprint,
        descriptor_id="desc-1",
        draft_id="proposal-draft",
        bundle_id="bundle-1",
        model_version="v1",
        auth_context=auth(Permission.ASSEMBLY_WRITE, LifecycleRole.AUTHOR),
    )
    assert result.draft.assertion_count == 1
    assert result.draft.pending_count == 1