"""Unit tests for discovery proposal adaptation into assembly drafts."""

from __future__ import annotations

import pytest

from nl2data_core.assembly import (
    AssertionProvenanceKind,
    AssertionType,
    ReviewState,
    adapt_approved_proposals,
    create_discovery_draft,
)
from nl2data_core.metadata import (
    MetadataConfidence,
    MetadataTrustLevel,
    ProposalStatus,
    SemanticProposal,
    SemanticProposalKind,
    SemanticProposalSet,
)


def fp(value: str) -> str:
    return f"sha256:{value * 64}"


def proposal(
    proposal_id: str,
    *,
    kind: SemanticProposalKind,
    target_id: str,
    fact: dict[str, object],
    status: ProposalStatus,
    trust_level: MetadataTrustLevel = MetadataTrustLevel.INFERRED,
) -> SemanticProposal:
    return SemanticProposal(
        proposal_id=proposal_id,
        kind=kind,
        target_id=target_id,
        fact=fact,
        trust_level=trust_level,
        method="test-inference",
        confidence=MetadataConfidence(value=0.9, method="test-inference"),
        evidence_fingerprint=fp("b"),
        snapshot_fingerprint=fp("a"),
        status=status,
    )


def proposal_set() -> SemanticProposalSet:
    return SemanticProposalSet(
        snapshot_fingerprint=fp("a"),
        proposals=(
            proposal(
                "entity-orders",
                kind=SemanticProposalKind.ENTITY,
                target_id="orders",
                fact={"entity_id": "orders", "label": "Orders"},
                status=ProposalStatus.APPROVED,
                trust_level=MetadataTrustLevel.OBSERVED,
            ),
            proposal(
                "field-status",
                kind=SemanticProposalKind.FIELD,
                target_id="status",
                fact={
                    "entity_id": "orders",
                    "field_id": "status",
                    "label": "Status",
                    "data_type": "string",
                },
                status=ProposalStatus.APPROVED,
            ),
            proposal(
                "alias-status",
                kind=SemanticProposalKind.ALIAS,
                target_id="status",
                fact={"field_id": "status", "alias": "order state"},
                status=ProposalStatus.APPROVED,
            ),
            proposal(
                "entity-customers",
                kind=SemanticProposalKind.ENTITY,
                target_id="customers",
                fact={"entity_id": "customers", "label": "Customers"},
                status=ProposalStatus.PENDING,
            ),
        ),
    )


def test_only_approved_proposals_become_pending_assertions() -> None:
    assertions = adapt_approved_proposals(proposal_set(), descriptor_id="sales")
    assert len(assertions) == 3
    assert all(item.review_state is ReviewState.PENDING for item in assertions)
    assert {item.type for item in assertions} == {
        AssertionType.ENTITY,
        AssertionType.FIELD,
        AssertionType.MAPPING,
    }
    assert all(
        item.provenance.proposal_reference != "entity-customers"
        for item in assertions
    )


def test_proposal_provenance_remains_audit_side_metadata() -> None:
    assertions = adapt_approved_proposals(proposal_set(), descriptor_id="sales")
    observed = next(item for item in assertions if item.type is AssertionType.ENTITY)
    inferred = next(item for item in assertions if item.type is AssertionType.FIELD)
    assert observed.provenance.kind is AssertionProvenanceKind.DISCOVERED
    assert inferred.provenance.kind is AssertionProvenanceKind.INFERRED
    assert observed.provenance.snapshot_fingerprint == fp("a")
    assert "provenance" not in observed.canonical_payload()


def test_alias_uses_field_entity_scope_for_stable_mapping_identity() -> None:
    assertions = adapt_approved_proposals(proposal_set(), descriptor_id="sales")
    mapping = next(item for item in assertions if item.type is AssertionType.MAPPING)
    assert mapping.payload["entity_id"] == "orders"
    assert mapping.payload["field_id"] == "status"


def test_stale_proposal_snapshot_is_rejected_before_draft_creation() -> None:
    with pytest.raises(ValueError, match="snapshot fingerprint is stale"):
        create_discovery_draft(
            proposal_set(),
            descriptor_id="sales",
            draft_id="draft-1",
            bundle_id="sales",
            source_id="sales",
            model_version="1.0.0",
            author_reference="team-analytics",
            expected_snapshot_fingerprint=fp("c"),
        )


def test_current_snapshot_creates_bound_discovery_draft() -> None:
    draft = create_discovery_draft(
        proposal_set(),
        descriptor_id="sales",
        draft_id="draft-1",
        bundle_id="sales",
        source_id="sales",
        model_version="1.0.0",
        author_reference="team-analytics",
        expected_snapshot_fingerprint=fp("a"),
    )
    assert draft.source_snapshot_fingerprint == fp("a")
    assert len(draft.assertions) == 3