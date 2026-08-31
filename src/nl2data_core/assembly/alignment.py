"""Incremental rediscovery alignment by semantic assertion identity."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .manifest import AcceptedAssertionManifest
from .models import AssemblyDraft, AssertionType, ReviewState, SemanticAssertion

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_MAX_CHANGES = 16_384


class AssertionChangeKind(StrEnum):
    """Incremental difference classification for one assertion identity."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    STALE = "stale"
    PREVIOUSLY_REJECTED = "previously_rejected"


class AssertionAlignmentChange(BaseModel):
    """One bounded incremental assertion difference."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: AssertionChangeKind
    assertion_id: str = Field(pattern=_FINGERPRINT_PATTERN)
    assertion_type: AssertionType
    previous_payload_hash: str | None = Field(
        default=None,
        pattern=_FINGERPRINT_PATTERN,
    )
    previous_review_state: ReviewState | None = None
    candidate: SemanticAssertion | None = None


class AssertionAlignment(BaseModel):
    """Bounded changes requiring attention after incremental rediscovery."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    baseline_kind: Literal["draft", "published_manifest"]
    changes: tuple[AssertionAlignmentChange, ...] = Field(
        default_factory=tuple,
        max_length=_MAX_CHANGES,
    )

    def by_kind(
        self,
        kind: AssertionChangeKind,
    ) -> tuple[AssertionAlignmentChange, ...]:
        return tuple(change for change in self.changes if change.kind is kind)


def align_assertions(
    candidates: tuple[SemanticAssertion, ...],
    *,
    baseline: AssemblyDraft | AcceptedAssertionManifest,
    discovery_complete: bool = True,
    replay_rejected: bool = False,
) -> AssertionAlignment:
    """Align rediscovered candidates against a draft or published manifest."""
    candidate_by_id = {assertion.id: assertion for assertion in candidates}
    if len(candidate_by_id) != len(candidates):
        raise ValueError("rediscovery candidates must have unique assertion ids")

    if isinstance(baseline, AssemblyDraft):
        baseline_kind: Literal["draft", "published_manifest"] = "draft"
        baseline_items = {
            assertion.id: (
                assertion.type,
                assertion.payload_hash(),
                assertion.review_state,
            )
            for assertion in baseline.assertions
        }
    else:
        baseline_kind = "published_manifest"
        baseline_items = {
            assertion.id: (
                assertion.type,
                assertion.payload_hash,
                ReviewState.APPROVED,
            )
            for assertion in baseline.assertions
        }

    changes: list[AssertionAlignmentChange] = []
    for assertion in candidates:
        previous = baseline_items.get(assertion.id)
        if previous is None:
            changes.append(
                AssertionAlignmentChange(
                    kind=AssertionChangeKind.ADDED,
                    assertion_id=assertion.id,
                    assertion_type=assertion.type,
                    candidate=assertion,
                )
            )
        elif previous[1] != assertion.payload_hash():
            changes.append(
                AssertionAlignmentChange(
                    kind=AssertionChangeKind.MODIFIED,
                    assertion_id=assertion.id,
                    assertion_type=assertion.type,
                    previous_payload_hash=previous[1],
                    candidate=assertion,
                )
            )
        elif previous[2] is ReviewState.REJECTED and replay_rejected:
            changes.append(
                AssertionAlignmentChange(
                    kind=AssertionChangeKind.PREVIOUSLY_REJECTED,
                    assertion_id=assertion.id,
                    assertion_type=assertion.type,
                    previous_payload_hash=previous[1],
                    previous_review_state=ReviewState.REJECTED,
                    candidate=assertion,
                )
            )

    missing_kind = (
        AssertionChangeKind.DELETED
        if discovery_complete
        else AssertionChangeKind.STALE
    )
    for assertion_id, (assertion_type, payload_hash, review_state) in baseline_items.items():
        if (
            assertion_id not in candidate_by_id
            and review_state is not ReviewState.REJECTED
        ):
            changes.append(
                AssertionAlignmentChange(
                    kind=missing_kind,
                    assertion_id=assertion_id,
                    assertion_type=assertion_type,
                    previous_payload_hash=payload_hash,
                    previous_review_state=review_state,
                )
            )

    return AssertionAlignment(
        baseline_kind=baseline_kind,
        changes=tuple(sorted(changes, key=lambda change: change.assertion_id)),
    )