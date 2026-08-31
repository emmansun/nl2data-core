"""Admin Verification Suite operations and safe DTO contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from nl2data_core.assembly import (
    ASSEMBLY_API_VERSION,
    AssemblyDraft,
    AssemblyState,
    AssertionProvenance,
    AssertionType,
    ReviewState,
    SemanticAssertion,
)
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.governance.models import PolicyScope
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.verification.execution import VerificationExecutionContext
from nl2data_core.verification.models import VerificationPlan
from nl2data_core.verification.policy import COMPATIBILITY_POLICY
from nl2data_core.verification.suite import VerificationSuiteRunner

from helpers import _FakeDependencies, _make_auth
from nl2data_admin_service.auth import Permission
from nl2data_admin_service.config import AdminServiceConfig
from nl2data_admin_service.dtos import (
    DraftRevisionCommand,
    PublishDraftCommand,
    VerifyDraftCommand,
)
from nl2data_admin_service.errors import (
    AuthorizationDeniedError,
    ConflictError,
    ValidationError,
)
from nl2data_admin_service.schema import build_schema
from nl2data_admin_service.service import AdminService


class StubExecutor:
    executor_id = "admin-stub"
    capability_ids = frozenset()
    capability_fingerprint = "sha256:" + "9" * 64

    async def open_session(self, fixture_profile_id, context):
        raise NotImplementedError

    async def execute(self, ir, session, context):
        raise NotImplementedError

    async def run_case(self, ir, *, fixture_profile_id, context):
        raise NotImplementedError


class ContextFactory:
    def create(self, *, draft, candidate, manifest, policy, auth_context):
        return VerificationExecutionContext(
            candidate=candidate,
            manifest=manifest,
            view=AuthorizedView(
                source_id=draft.source_id,
                root_entity_ids=frozenset({"orders"}),
            ),
            policy=policy,
            policy_scope=PolicyScope(
                policy_id="admin-verification",
                source_ids=frozenset({draft.source_id}),
                resource_ids=frozenset({"orders"}),
                operation_ids=frozenset({"select"}),
            ),
            tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
            source_scope_fingerprint=sha256_fingerprint(
                {"source_id": draft.source_id}
            ),
            deadline_at=datetime.now(UTC) + timedelta(seconds=10),
        )


def approved_draft() -> AssemblyDraft:
    assertion = SemanticAssertion.create(
        type=AssertionType.ENTITY,
        payload={
            "descriptor_id": "desc-1",
            "entity_id": "orders",
            "label": "Orders",
        },
        provenance=AssertionProvenance(kind="manual"),
    ).bind_review(
        state=ReviewState.APPROVED,
        reviewer_reference="reviewer-1",
    )
    return AssemblyDraft(
        apiVersion=ASSEMBLY_API_VERSION,
        draft_id="draft-verify",
        bundle_id="bundle-1",
        source_id="source-1",
        model_version="v1",
        state=AssemblyState.APPROVED,
        draft_revision=3,
        assertions=(assertion,),
        author_reference="author-1",
        approved_by="approver-1",
    )


@pytest.fixture
def verification_service():
    dependencies = _FakeDependencies()
    dependencies.verification_executor = StubExecutor()
    dependencies.verification_context_factory = ContextFactory()
    dependencies.verification_policies = {}
    service = AdminService(dependencies, AdminServiceConfig())
    draft = approved_draft()
    dependencies.draft_store.create(
        draft,
        tenant_scope_fingerprint="sha256:" + "0" * 64,
    )
    return service, dependencies, draft


@pytest.mark.asyncio
async def test_verify_draft_is_permissioned_side_effect_free_and_redacted(
    verification_service,
) -> None:
    service, dependencies, draft = verification_service
    before = draft.file_payload()
    result = await service.verify_draft(
        draft.draft_id,
        VerifyDraftCommand(
            expected_revision=3,
            policy_profile="compatibility-v1",
        ),
        auth_context=_make_auth([Permission.ASSEMBLY_VERIFY]),
    )
    assert result.verification.status == "passed"
    assert tuple(layer.status for layer in result.verification.layers) == (
        "passed",
        "not_run",
        "not_run",
    )
    stored = dependencies.draft_store.get(
        draft.draft_id,
        tenant_scope_fingerprint="sha256:" + "0" * 64,
    )
    assert stored is not None and stored.file_payload() == before
    assert not dependencies.lifecycle_catalog.versions(
        draft.bundle_id,
        tenant_scope_fingerprint="sha256:" + "0" * 64,
    )
    serialized = result.model_dump_json()
    for forbidden in (
        "query_fingerprint",
        "result_fingerprint",
        "tenant_scope_fingerprint",
        "source_scope_fingerprint",
        "expected",
        "actual",
        "deployment",
        "password",
    ):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_verify_draft_denies_permission_stale_revision_and_missing_executor(
    verification_service,
) -> None:
    service, dependencies, draft = verification_service
    command = VerifyDraftCommand(
        expected_revision=3, policy_profile="compatibility-v1"
    )
    with pytest.raises(AuthorizationDeniedError):
        await service.verify_draft(
            draft.draft_id,
            command,
            auth_context=_make_auth([]),
        )
    with pytest.raises(ConflictError):
        await service.verify_draft(
            draft.draft_id,
            command.model_copy(update={"expected_revision": 2}),
            auth_context=_make_auth([Permission.ASSEMBLY_VERIFY]),
        )
    dependencies.verification_executor = None
    with pytest.raises(ValidationError, match="executor"):
        await service.verify_draft(
            draft.draft_id,
            command,
            auth_context=_make_auth([Permission.ASSEMBLY_VERIFY]),
        )


def test_published_verification_inspection_returns_original_safe_identity(
    verification_service,
) -> None:
    service, _, draft = verification_service
    published = service.publish_draft(
        draft.draft_id,
        DraftRevisionCommand(expected_revision=3),
        auth_context=_make_auth(
            [Permission.BUNDLE_PUBLISH],
            lifecycle_roles=frozenset({"publisher"}),
        ),
    )
    detail = service.get_verification_evidence(
        draft.bundle_id,
        published.fingerprint,
        auth_context=_make_auth([Permission.ASSEMBLY_AUDIT]),
    )
    assert detail.evidence_reference == published.verification_evidence_reference
    assert detail.policy_profile == "compatibility-v1"
    assert detail.executor_id is None


def test_verification_capability_and_schema_are_registered(
    verification_service,
) -> None:
    service, _, _ = verification_service
    capabilities = service.capabilities()
    assert any(
        capability.permission is Permission.ASSEMBLY_VERIFY
        for capability in capabilities.capabilities
    )
    schema = build_schema("v1")
    assert "VerifyDraftCommand" in schema.commands
    assert "PublishDraftCommand" in schema.commands
    assert "DraftVerificationResult" in schema.results
    assert "VerificationEvidenceReference" in schema.results


@pytest.mark.asyncio
async def test_planned_publish_consumes_exact_evidence_and_blocks_missing() -> None:
    dependencies = _FakeDependencies()
    dependencies.verification_executor = StubExecutor()
    dependencies.verification_context_factory = ContextFactory()
    dependencies.verification_policies = {}
    service = AdminService(dependencies, AdminServiceConfig())
    plan = VerificationPlan(policy_profile="compatibility-v1")
    draft = AssemblyDraft.model_validate(
        {
            **approved_draft().model_dump(mode="python", by_alias=True),
            "verification_plan": plan,
            "approved_verification_plan_fingerprint": plan.fingerprint,
        }
    )
    dependencies.draft_store.create(
        draft,
        tenant_scope_fingerprint="sha256:" + "0" * 64,
    )
    publish_auth = _make_auth(
        [Permission.BUNDLE_PUBLISH],
        lifecycle_roles=frozenset({"publisher"}),
    )
    missing = service.publish_draft(
        draft.draft_id,
        PublishDraftCommand(
            expected_revision=3,
            policy_profile="compatibility-v1",
        ),
        auth_context=publish_auth,
    )
    assert not missing.success
    assert missing.issues[0].code == "verification_evidence_required"

    context, structural = service._create_verification_context(
        draft,
        policy=COMPATIBILITY_POLICY,
        auth_context=publish_auth,
    )
    evidence = await VerificationSuiteRunner(
        executor=dependencies.verification_executor
    ).run(
        plan=plan,
        policy=COMPATIBILITY_POLICY,
        structural_evidence=structural.evidence,
        context=context,
        draft_id=draft.draft_id,
        draft_revision=draft.draft_revision,
    )
    published = service.publish_draft(
        draft.draft_id,
        PublishDraftCommand(
            expected_revision=3,
            policy_profile="compatibility-v1",
            verification_evidence=evidence,
        ),
        auth_context=publish_auth,
    )
    assert published.success
    assert published.verification_evidence_reference == (
        f"verification-{evidence.fingerprint.removeprefix('sha256:')[:24]}"
    )