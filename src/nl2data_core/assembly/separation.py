"""Configurable separation-of-duties policy for semantic publication."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nl2data_core.views.models import validate_safe_description

_MAX_REFERENCE_CHARS = 256
_MAX_REASON_CHARS = 1_024
_MAX_REVIEWERS = 64


class SeparationOfDutiesMode(StrEnum):
    """Supported host-selected lifecycle role-separation policies."""

    STRICT = "strict"
    REVIEW_APPROVAL_SPLIT = "review_approval_split"
    SOLO_WITH_WAIVER = "solo_with_waiver"


class SeparationOfDutiesWaiver(BaseModel):
    """Bounded audit record permitting lifecycle role overlap."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    waiver_reference: str = Field(min_length=1, max_length=_MAX_REFERENCE_CHARS)
    reason: str = Field(min_length=1, max_length=_MAX_REASON_CHARS)
    overlapping_operator_references: tuple[str, ...] = Field(
        min_length=1,
        max_length=4,
    )

    @field_validator("waiver_reference", "reason")
    @classmethod
    def _safe_text(cls, value: str) -> str:
        return validate_safe_description(value)


class SeparationOfDutiesDecision(BaseModel):
    """Safe policy result included in publish audit metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    mode: SeparationOfDutiesMode
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    waiver: SeparationOfDutiesWaiver | None = None


def _overlapping_references(
    *,
    author_reference: str,
    reviewer_references: tuple[str, ...],
    approver_reference: str,
    publisher_reference: str,
) -> tuple[str, ...]:
    roles = (
        author_reference,
        *set(reviewer_references),
        approver_reference,
        publisher_reference,
    )
    return tuple(sorted({reference for reference in roles if roles.count(reference) > 1}))


def evaluate_separation_of_duties(
    *,
    mode: SeparationOfDutiesMode,
    author_reference: str,
    reviewer_references: tuple[str, ...],
    approver_reference: str,
    publisher_reference: str,
    waiver_reference: str | None = None,
    waiver_reason: str | None = None,
) -> SeparationOfDutiesDecision:
    """Evaluate lifecycle identities under the selected host policy mode."""
    if not reviewer_references:
        return SeparationOfDutiesDecision(
            allowed=False,
            mode=mode,
            reason_code="reviewer_required",
        )
    if len(reviewer_references) > _MAX_REVIEWERS:
        raise ValueError(f"reviewer references are limited to {_MAX_REVIEWERS}")
    overlaps = _overlapping_references(
        author_reference=author_reference,
        reviewer_references=reviewer_references,
        approver_reference=approver_reference,
        publisher_reference=publisher_reference,
    )
    if mode is SeparationOfDutiesMode.STRICT:
        return SeparationOfDutiesDecision(
            allowed=not overlaps,
            mode=mode,
            reason_code="authorized" if not overlaps else "role_overlap",
        )
    if mode is SeparationOfDutiesMode.REVIEW_APPROVAL_SPLIT:
        split = approver_reference not in reviewer_references
        return SeparationOfDutiesDecision(
            allowed=split,
            mode=mode,
            reason_code="authorized" if split else "review_approval_overlap",
        )
    if not overlaps:
        return SeparationOfDutiesDecision(
            allowed=True,
            mode=mode,
            reason_code="authorized",
        )
    if waiver_reference is None or waiver_reason is None:
        return SeparationOfDutiesDecision(
            allowed=False,
            mode=mode,
            reason_code="waiver_required",
        )
    waiver = SeparationOfDutiesWaiver(
        waiver_reference=waiver_reference,
        reason=waiver_reason,
        overlapping_operator_references=overlaps,
    )
    return SeparationOfDutiesDecision(
        allowed=True,
        mode=mode,
        reason_code="waiver_applied",
        waiver=waiver,
    )