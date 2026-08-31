"""Revision-guarded semantic assembly review and approval operations."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nl2data_core.views.models import validate_safe_description

from .authorization import (
    LifecycleAction,
    LifecycleAuthorizationContext,
    LifecycleAuthorizer,
    LifecycleRole,
    require_lifecycle_authorization,
)
from .models import (
    AssemblyDraft,
    AssemblyState,
    AssertionProvenance,
    AssertionProvenanceKind,
    ReviewState,
    SemanticAssertion,
)

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_MAX_REFERENCE_CHARS = 256
_MAX_REASON_CHARS = 1_024


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AssertionDecisionKind(StrEnum):
    """Audited assertion-level lifecycle actions."""

    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"


class AssertionDecisionRecord(BaseModel):
    """Bounded audit metadata for one assertion mutation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    draft_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    action: AssertionDecisionKind
    assertion_id: str = Field(pattern=_FINGERPRINT_PATTERN)
    resulting_assertion_id: str = Field(pattern=_FINGERPRINT_PATTERN)
    operator_reference: str = Field(min_length=1, max_length=_MAX_REFERENCE_CHARS)
    previous_payload_hash: str = Field(pattern=_FINGERPRINT_PATTERN)
    resulting_payload_hash: str = Field(pattern=_FINGERPRINT_PATTERN)
    previous_provenance: AssertionProvenance
    occurred_at: datetime = Field(default_factory=_utc_now)
    reason: str = Field(default="", max_length=_MAX_REASON_CHARS)

    @field_validator("operator_reference", "reason")
    @classmethod
    def _safe_text(cls, value: str) -> str:
        return validate_safe_description(value)


class AssertionMutationOutcome(BaseModel):
    """Updated immutable draft and its corresponding assertion audit record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    draft: AssemblyDraft
    record: AssertionDecisionRecord


class DraftLifecycleAction(StrEnum):
    """Audited draft-level review and approval actions."""

    SUBMIT_FOR_REVIEW = "submit_for_review"
    APPROVE = "approve"


class DraftLifecycleRecord(BaseModel):
    """Bounded audit metadata for one draft state transition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    draft_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    action: DraftLifecycleAction
    operator_reference: str = Field(min_length=1, max_length=_MAX_REFERENCE_CHARS)
    previous_revision: int = Field(ge=0)
    resulting_revision: int = Field(ge=1)
    occurred_at: datetime = Field(default_factory=_utc_now)

    @field_validator("operator_reference")
    @classmethod
    def _safe_operator(cls, value: str) -> str:
        return validate_safe_description(value)


class DraftLifecycleOutcome(BaseModel):
    """Updated immutable draft and its draft-level audit record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    draft: AssemblyDraft
    record: DraftLifecycleRecord


def _target(draft: AssemblyDraft, assertion_id: str) -> SemanticAssertion:
    if draft.state is not AssemblyState.REVIEW:
        raise ValueError("assertion decisions require a draft in review")
    for assertion in draft.assertions:
        if assertion.id == assertion_id:
            return assertion
    raise ValueError(f"unknown assertion id: {assertion_id}")


def _replace(
    draft: AssemblyDraft,
    *,
    expected_revision: int,
    original: SemanticAssertion,
    replacement: SemanticAssertion,
    action: AssertionDecisionKind,
    operator_reference: str,
    reason: str,
) -> AssertionMutationOutcome:
    assertions = tuple(
        replacement if assertion.id == original.id else assertion
        for assertion in draft.assertions
    )
    updated = draft.mutate(
        expected_revision=expected_revision,
        assertions=assertions,
    )
    return AssertionMutationOutcome(
        draft=updated,
        record=AssertionDecisionRecord(
            draft_id=draft.draft_id,
            action=action,
            assertion_id=original.id,
            resulting_assertion_id=replacement.id,
            operator_reference=operator_reference,
            previous_payload_hash=original.payload_hash(),
            resulting_payload_hash=replacement.payload_hash(),
            previous_provenance=original.provenance,
            reason=reason,
        ),
    )


def decide_assertion(
    draft: AssemblyDraft,
    *,
    assertion_id: str,
    decision: ReviewState,
    expected_revision: int,
    authorization: LifecycleAuthorizationContext,
    authorizer: LifecycleAuthorizer,
    reason: str = "",
) -> AssertionMutationOutcome:
    """Approve or reject one assertion against the observed draft revision."""
    require_lifecycle_authorization(
        context=authorization,
        authorizer=authorizer,
        required_role=LifecycleRole.REVIEWER,
        action=LifecycleAction.REVIEW_ASSERTION,
        resource_id=draft.draft_id,
    )
    if decision not in (ReviewState.APPROVED, ReviewState.REJECTED):
        raise ValueError("assertion decisions must be approved or rejected")
    original = _target(draft, assertion_id)
    if (
        draft.source_snapshot_fingerprint is not None
        and original.provenance.kind
        in {AssertionProvenanceKind.DISCOVERED, AssertionProvenanceKind.INFERRED}
        and original.provenance.snapshot_fingerprint
        != draft.source_snapshot_fingerprint
    ):
        raise ValueError("assertion provenance is stale for the draft source snapshot")
    replacement = original.bind_review(
        state=decision,
        reviewer_reference=authorization.operator_reference,
        reason=reason,
    )
    action = (
        AssertionDecisionKind.APPROVE
        if decision is ReviewState.APPROVED
        else AssertionDecisionKind.REJECT
    )
    return _replace(
        draft,
        expected_revision=expected_revision,
        original=original,
        replacement=replacement,
        action=action,
        operator_reference=authorization.operator_reference,
        reason=reason,
    )


def edit_assertion(
    draft: AssemblyDraft,
    *,
    assertion_id: str,
    payload: Mapping[str, Any],
    expected_revision: int,
    authorization: LifecycleAuthorizationContext,
    authorizer: LifecycleAuthorizer,
    reason: str = "",
) -> AssertionMutationOutcome:
    """Apply a human edit, transfer responsibility, and invalidate review."""
    require_lifecycle_authorization(
        context=authorization,
        authorizer=authorizer,
        required_role=LifecycleRole.REVIEWER,
        action=LifecycleAction.EDIT_ASSERTION,
        resource_id=draft.draft_id,
    )
    original = _target(draft, assertion_id)
    replacement = SemanticAssertion.create(
        type=original.type,
        payload=payload,
        provenance=AssertionProvenance(
            kind=AssertionProvenanceKind.MANUAL,
            source_reference=original.provenance.source_reference or f"seed:{original.id}",
            proposal_reference=original.provenance.proposal_reference,
            snapshot_fingerprint=original.provenance.snapshot_fingerprint,
            evidence_fingerprint=original.provenance.evidence_fingerprint,
            method=original.provenance.method,
        ),
    )
    return _replace(
        draft,
        expected_revision=expected_revision,
        original=original,
        replacement=replacement,
        action=AssertionDecisionKind.EDIT,
        operator_reference=authorization.operator_reference,
        reason=reason,
    )


def _transition(
    draft: AssemblyDraft,
    *,
    expected_revision: int,
    state: AssemblyState,
    action: DraftLifecycleAction,
    operator_reference: str,
) -> DraftLifecycleOutcome:
    updated = draft.transition(
        expected_revision=expected_revision,
        state=state,
    )
    metadata_field = (
        "review_submitted_by"
        if action is DraftLifecycleAction.SUBMIT_FOR_REVIEW
        else "approved_by"
    )
    updated = AssemblyDraft.model_validate(
        {
            **updated.model_dump(mode="python", by_alias=True),
            metadata_field: operator_reference,
        }
    )
    return DraftLifecycleOutcome(
        draft=updated,
        record=DraftLifecycleRecord(
            draft_id=draft.draft_id,
            action=action,
            operator_reference=operator_reference,
            previous_revision=draft.draft_revision,
            resulting_revision=updated.draft_revision,
        ),
    )


def submit_for_review(
    draft: AssemblyDraft,
    *,
    expected_revision: int,
    authorization: LifecycleAuthorizationContext,
    authorizer: LifecycleAuthorizer,
) -> DraftLifecycleOutcome:
    """Freeze the authored revision as the draft entering review."""
    require_lifecycle_authorization(
        context=authorization,
        authorizer=authorizer,
        required_role=LifecycleRole.AUTHOR,
        action=LifecycleAction.SUBMIT_FOR_REVIEW,
        resource_id=draft.draft_id,
    )
    return _transition(
        draft,
        expected_revision=expected_revision,
        state=AssemblyState.REVIEW,
        action=DraftLifecycleAction.SUBMIT_FOR_REVIEW,
        operator_reference=authorization.operator_reference,
    )


def approve_draft(
    draft: AssemblyDraft,
    *,
    expected_revision: int,
    authorization: LifecycleAuthorizationContext,
    authorizer: LifecycleAuthorizer,
) -> DraftLifecycleOutcome:
    """Approve a fully reviewed draft and freeze its semantic content."""
    require_lifecycle_authorization(
        context=authorization,
        authorizer=authorizer,
        required_role=LifecycleRole.APPROVER,
        action=LifecycleAction.APPROVE_DRAFT,
        resource_id=draft.draft_id,
    )
    return _transition(
        draft,
        expected_revision=expected_revision,
        state=AssemblyState.APPROVED,
        action=DraftLifecycleAction.APPROVE,
        operator_reference=authorization.operator_reference,
    )