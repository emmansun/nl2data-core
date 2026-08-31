"""Unit tests for authorized semantic assembly review and approval."""

from __future__ import annotations

import pytest

from nl2data_core.assembly import (
    ASSEMBLY_API_VERSION,
    AssemblyDraft,
    AssemblyState,
    AssertionProvenance,
    AssertionProvenanceKind,
    AssertionType,
    LifecycleAuthorizationContext,
    LifecycleAuthorizationDecision,
    LifecycleAuthorizationError,
    LifecycleAuthorizationRequest,
    LifecycleRole,
    ReviewState,
    SemanticAssertion,
    SeparationOfDutiesMode,
    approve_draft,
    decide_assertion,
    edit_assertion,
    evaluate_separation_of_duties,
    submit_for_review,
)


class Authorizer:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.requests: list[LifecycleAuthorizationRequest] = []

    def authorize(
        self,
        request: LifecycleAuthorizationRequest,
    ) -> LifecycleAuthorizationDecision:
        self.requests.append(request)
        return LifecycleAuthorizationDecision(
            allowed=self.allowed,
            reason_code="authorized" if self.allowed else "scope_denied",
        )


def authorization(
    role: LifecycleRole,
    *,
    operator: str,
) -> LifecycleAuthorizationContext:
    return LifecycleAuthorizationContext(
        operator_reference=operator,
        tenant_scope_fingerprint="sha256:" + "a" * 64,
        source_id="sales",
        roles=frozenset({role}),
    )


def llm_assertion() -> SemanticAssertion:
    return SemanticAssertion.create(
        type=AssertionType.ENTITY,
        payload={
            "descriptor_id": "sales",
            "entity_id": "orders",
            "label": "Orders",
        },
        provenance=AssertionProvenance(
            kind=AssertionProvenanceKind.LLM_SUGGESTED,
            source_reference="suggestion-seed",
        ),
    )


def draft() -> AssemblyDraft:
    return AssemblyDraft(
        apiVersion=ASSEMBLY_API_VERSION,
        draft_id="draft-1",
        bundle_id="sales",
        source_id="sales",
        model_version="1.0.0",
        assertions=(llm_assertion(),),
        author_reference="author-1",
    )


def review_draft() -> AssemblyDraft:
    return draft().transition(expected_revision=0, state=AssemblyState.REVIEW)


def test_missing_role_and_host_denial_leave_draft_unchanged() -> None:
    original = draft()
    no_role = LifecycleAuthorizationContext(
        operator_reference="operator-1",
        tenant_scope_fingerprint="sha256:" + "a" * 64,
        source_id="sales",
        roles=frozenset(),
    )
    hook = Authorizer()
    with pytest.raises(LifecycleAuthorizationError) as missing:
        submit_for_review(
            original,
            expected_revision=0,
            authorization=no_role,
            authorizer=hook,
        )
    assert missing.value.reason_code == "missing_lifecycle_role"
    assert not hook.requests

    denied = Authorizer(allowed=False)
    with pytest.raises(LifecycleAuthorizationError) as rejected:
        submit_for_review(
            original,
            expected_revision=0,
            authorization=authorization(LifecycleRole.AUTHOR, operator="author-1"),
            authorizer=denied,
        )
    assert rejected.value.reason_code == "scope_denied"
    assert original.draft_revision == 0


def test_llm_suggestion_requires_explicit_review_before_approval() -> None:
    review = review_draft()
    with pytest.raises(ValueError, match="all assertions to be reviewed"):
        approve_draft(
            review,
            expected_revision=1,
            authorization=authorization(
                LifecycleRole.APPROVER,
                operator="approver-1",
            ),
            authorizer=Authorizer(),
        )
    reviewed = decide_assertion(
        review,
        assertion_id=review.assertions[0].id,
        decision=ReviewState.APPROVED,
        expected_revision=1,
        authorization=authorization(LifecycleRole.REVIEWER, operator="reviewer-1"),
        authorizer=Authorizer(),
    ).draft
    approved = approve_draft(
        reviewed,
        expected_revision=2,
        authorization=authorization(LifecycleRole.APPROVER, operator="approver-1"),
        authorizer=Authorizer(),
    ).draft
    assert approved.state is AssemblyState.APPROVED
    assert approved.assertions[0].provenance.kind is AssertionProvenanceKind.LLM_SUGGESTED


def test_human_edit_transfers_responsibility_and_preserves_seed_audit() -> None:
    review = review_draft()
    original = review.assertions[0]
    outcome = edit_assertion(
        review,
        assertion_id=original.id,
        payload={**dict(original.payload), "label": "Sales orders"},
        expected_revision=1,
        authorization=authorization(LifecycleRole.REVIEWER, operator="reviewer-1"),
        authorizer=Authorizer(),
    )
    changed = outcome.draft.assertions[0]
    assert changed.id == original.id
    assert changed.review_state is ReviewState.PENDING
    assert changed.provenance.kind is AssertionProvenanceKind.MANUAL
    assert changed.provenance.source_reference == "suggestion-seed"
    assert outcome.record.previous_provenance.kind is AssertionProvenanceKind.LLM_SUGGESTED


def test_human_edit_preserves_discovery_audit_references() -> None:
    discovered = SemanticAssertion.create(
        type=AssertionType.ENTITY,
        payload={
            "descriptor_id": "sales",
            "entity_id": "orders",
            "label": "Orders",
        },
        provenance=AssertionProvenance(
            kind=AssertionProvenanceKind.DISCOVERED,
            source_reference="catalog:orders",
            proposal_reference="proposal-1",
            snapshot_fingerprint="sha256:" + "b" * 64,
            evidence_fingerprint="sha256:" + "c" * 64,
            method="metadata-inspection",
        ),
    )
    review = draft().model_copy(update={"state": AssemblyState.REVIEW, "assertions": (discovered,)})
    changed = edit_assertion(
        review,
        assertion_id=discovered.id,
        payload={**dict(discovered.payload), "label": "Sales orders"},
        expected_revision=0,
        authorization=authorization(LifecycleRole.REVIEWER, operator="reviewer-1"),
        authorizer=Authorizer(),
    ).draft.assertions[0]
    assert changed.provenance.kind is AssertionProvenanceKind.MANUAL
    assert changed.provenance.source_reference == "catalog:orders"
    assert changed.provenance.proposal_reference == "proposal-1"
    assert changed.provenance.snapshot_fingerprint == "sha256:" + "b" * 64
    assert changed.provenance.evidence_fingerprint == "sha256:" + "c" * 64
    assert changed.provenance.method == "metadata-inspection"


def test_stale_discovery_assertion_cannot_be_reviewed() -> None:
    discovered = SemanticAssertion.create(
        type=AssertionType.ENTITY,
        payload={
            "descriptor_id": "sales",
            "entity_id": "orders",
            "label": "Orders",
        },
        provenance=AssertionProvenance(
            kind=AssertionProvenanceKind.DISCOVERED,
            snapshot_fingerprint="sha256:" + "b" * 64,
        ),
    )
    review = draft().model_copy(
        update={
            "state": AssemblyState.REVIEW,
            "assertions": (discovered,),
            "source_snapshot_fingerprint": "sha256:" + "c" * 64,
        }
    )
    with pytest.raises(ValueError, match="stale"):
        decide_assertion(
            review,
            assertion_id=discovered.id,
            decision=ReviewState.APPROVED,
            expected_revision=0,
            authorization=authorization(LifecycleRole.REVIEWER, operator="reviewer-1"),
            authorizer=Authorizer(),
        )


def test_separation_of_duties_modes_and_solo_waiver() -> None:
    strict = evaluate_separation_of_duties(
        mode=SeparationOfDutiesMode.STRICT,
        author_reference="alice",
        reviewer_references=("bob",),
        approver_reference="bob",
        publisher_reference="carol",
    )
    split = evaluate_separation_of_duties(
        mode=SeparationOfDutiesMode.REVIEW_APPROVAL_SPLIT,
        author_reference="alice",
        reviewer_references=("bob",),
        approver_reference="carol",
        publisher_reference="alice",
    )
    blocked = evaluate_separation_of_duties(
        mode=SeparationOfDutiesMode.SOLO_WITH_WAIVER,
        author_reference="alice",
        reviewer_references=("alice",),
        approver_reference="alice",
        publisher_reference="alice",
    )
    waived = evaluate_separation_of_duties(
        mode=SeparationOfDutiesMode.SOLO_WITH_WAIVER,
        author_reference="alice",
        reviewer_references=("alice",),
        approver_reference="alice",
        publisher_reference="alice",
        waiver_reference="change-42",
        waiver_reason="Emergency solo release",
    )
    assert not strict.allowed
    assert split.allowed
    assert blocked.reason_code == "waiver_required"
    assert waived.allowed and waived.waiver is not None