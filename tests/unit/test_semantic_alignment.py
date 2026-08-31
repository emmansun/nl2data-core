"""Unit tests for accepted-assertion manifests and rediscovery alignment."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nl2data_core.assembly import (
    ASSEMBLY_API_VERSION,
    AcceptedAssertionManifest,
    AssemblyDraft,
    AssemblyState,
    AssertionChangeKind,
    AssertionProvenance,
    AssertionProvenanceKind,
    AssertionType,
    ReviewState,
    SemanticAssertion,
    align_assertions,
)


def fp(value: str) -> str:
    return f"sha256:{value * 64}"


def assertion(entity_id: str, label: str) -> SemanticAssertion:
    return SemanticAssertion.create(
        type=AssertionType.ENTITY,
        payload={
            "descriptor_id": "sales",
            "entity_id": entity_id,
            "label": label,
        },
        provenance=AssertionProvenance(
            kind=AssertionProvenanceKind.DISCOVERED,
            snapshot_fingerprint=fp("a"),
            evidence_fingerprint=fp("b"),
            method="metadata",
        ),
    )


def approved(assertion_value: SemanticAssertion) -> SemanticAssertion:
    return assertion_value.bind_review(
        state=ReviewState.APPROVED,
        reviewer_reference="reviewer-1",
    )


def approved_draft(*assertions: SemanticAssertion) -> AssemblyDraft:
    return AssemblyDraft(
        apiVersion=ASSEMBLY_API_VERSION,
        draft_id="draft-1",
        bundle_id="sales",
        source_id="sales",
        model_version="1.0.0",
        state=AssemblyState.APPROVED,
        draft_revision=3,
        assertions=assertions,
        author_reference="team-analytics",
    )


def test_manifest_contains_only_accepted_semantic_content() -> None:
    accepted = approved(assertion("orders", "Orders"))
    rejected = assertion("legacy", "Legacy").bind_review(
        state=ReviewState.REJECTED,
        reviewer_reference="reviewer-2",
        reason="obsolete",
    )
    manifest = AcceptedAssertionManifest.from_draft(
        approved_draft(accepted, rejected),
        bundle_fingerprint=fp("c"),
    )
    payload = manifest.canonical_payload()
    assert [item["id"] for item in payload["assertions"]] == [accepted.id]
    serialized = str(payload)
    for forbidden in ("reviewer", "review_binding", "provenance", "rejected"):
        assert forbidden not in serialized


def test_manifest_rejects_pending_or_tampered_assertions() -> None:
    with pytest.raises(ValueError, match="pending"):
        AcceptedAssertionManifest.from_draft(
            approved_draft(assertion("orders", "Orders")),
            bundle_fingerprint=fp("c"),
        )

    accepted = approved(assertion("orders", "Orders"))
    manifest = AcceptedAssertionManifest.from_draft(
        approved_draft(accepted),
        bundle_fingerprint=fp("c"),
    )
    item = manifest.assertions[0].model_dump(mode="python")
    item["payload_hash"] = fp("d")
    with pytest.raises(ValidationError, match="payload hash"):
        type(manifest.assertions[0]).model_validate(item)


def test_draft_and_published_manifest_alignment_are_equivalent() -> None:
    unchanged = approved(assertion("orders", "Orders"))
    modified = approved(assertion("customers", "Customers"))
    deleted = approved(assertion("legacy", "Legacy"))
    baseline = approved_draft(unchanged, modified, deleted)
    manifest = AcceptedAssertionManifest.from_draft(
        baseline,
        bundle_fingerprint=fp("c"),
    )
    candidates = (
        assertion("orders", "Orders"),
        assertion("customers", "Customer accounts"),
        assertion("products", "Products"),
    )

    from_draft = align_assertions(candidates, baseline=baseline)
    from_manifest = align_assertions(candidates, baseline=manifest)
    assert [change.kind for change in from_draft.changes] == [
        change.kind for change in from_manifest.changes
    ]
    assert not any(change.assertion_id == unchanged.id for change in from_draft.changes)
    assert len(from_draft.by_kind(AssertionChangeKind.ADDED)) == 1
    assert len(from_draft.by_kind(AssertionChangeKind.MODIFIED)) == 1
    assert len(from_draft.by_kind(AssertionChangeKind.DELETED)) == 1
    changed = from_draft.by_kind(AssertionChangeKind.MODIFIED)[0].candidate
    assert changed is not None
    assert changed.review_state is ReviewState.PENDING


def test_incomplete_discovery_marks_missing_baseline_assertions_stale() -> None:
    baseline = approved_draft(approved(assertion("orders", "Orders")))
    result = align_assertions((), baseline=baseline, discovery_complete=False)
    assert len(result.by_kind(AssertionChangeKind.STALE)) == 1
    assert not result.by_kind(AssertionChangeKind.DELETED)


def test_rejected_candidates_are_optional_negative_evidence_only() -> None:
    candidate = assertion("legacy", "Legacy")
    rejected = candidate.bind_review(
        state=ReviewState.REJECTED,
        reviewer_reference="reviewer-1",
        reason="not a business entity",
    )
    baseline = approved_draft(rejected)

    default = align_assertions((candidate,), baseline=baseline)
    replayed = align_assertions(
        (candidate,),
        baseline=baseline,
        replay_rejected=True,
    )
    assert not default.changes
    replay = replayed.by_kind(AssertionChangeKind.PREVIOUSLY_REJECTED)
    assert len(replay) == 1
    assert replay[0].previous_review_state is ReviewState.REJECTED
    assert replay[0].candidate is not None
    assert replay[0].candidate.review_state is ReviewState.PENDING


def test_missing_rejected_negative_evidence_is_not_a_semantic_deletion() -> None:
    rejected = assertion("legacy", "Legacy").bind_review(
        state=ReviewState.REJECTED,
        reviewer_reference="reviewer-1",
    )
    result = align_assertions((), baseline=approved_draft(rejected))
    assert not result.changes