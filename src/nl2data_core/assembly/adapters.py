"""Adapters that converge discovery output into semantic assembly assertions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from nl2data_core.metadata.models import MetadataTrustLevel
from nl2data_core.metadata.proposals import (
    ProposalStatus,
    SemanticProposal,
    SemanticProposalKind,
    SemanticProposalSet,
)

from .models import (
    ASSEMBLY_API_VERSION,
    AssemblyDraft,
    AssertionProvenance,
    AssertionProvenanceKind,
    AssertionType,
    DeploymentBinding,
    SemanticAssertion,
)

_PROPOSAL_ASSERTION_TYPES = {
    SemanticProposalKind.ENTITY: AssertionType.ENTITY,
    SemanticProposalKind.FIELD: AssertionType.FIELD,
    SemanticProposalKind.RELATIONSHIP: AssertionType.RELATIONSHIP,
    SemanticProposalKind.GRAIN: AssertionType.GRAIN,
    SemanticProposalKind.MEASURE: AssertionType.MEASURE,
    SemanticProposalKind.ALIAS: AssertionType.MAPPING,
    SemanticProposalKind.CLASSIFICATION: AssertionType.POLICY,
    SemanticProposalKind.IDENTIFIER: AssertionType.POLICY,
}


def _provenance(proposal: SemanticProposal) -> AssertionProvenance:
    kind = (
        AssertionProvenanceKind.INFERRED
        if proposal.trust_level is MetadataTrustLevel.INFERRED
        else AssertionProvenanceKind.DISCOVERED
    )
    return AssertionProvenance(
        kind=kind,
        proposal_reference=proposal.proposal_id,
        snapshot_fingerprint=proposal.snapshot_fingerprint,
        evidence_fingerprint=proposal.evidence_fingerprint,
        method=proposal.method,
    )


def _field_scopes(proposal_set: SemanticProposalSet) -> dict[str, str]:
    scopes: dict[str, str] = {}
    for proposal in proposal_set.proposals:
        if proposal.kind is not SemanticProposalKind.FIELD:
            continue
        field_id = proposal.fact.get("field_id")
        entity_id = proposal.fact.get("entity_id")
        if isinstance(field_id, str) and isinstance(entity_id, str):
            existing = scopes.get(field_id)
            if existing is not None and existing != entity_id:
                raise ValueError(
                    f"field '{field_id}' has ambiguous entity scope in proposal set"
                )
            scopes[field_id] = entity_id
    return scopes


def _assertion_payload(
    proposal: SemanticProposal,
    *,
    descriptor_id: str,
    field_scopes: Mapping[str, str],
) -> dict[str, Any]:
    payload = {"descriptor_id": descriptor_id, **dict(proposal.fact)}
    identity_defaults = {
        SemanticProposalKind.ENTITY: "entity_id",
        SemanticProposalKind.FIELD: "field_id",
        SemanticProposalKind.RELATIONSHIP: "relationship_id",
        SemanticProposalKind.MEASURE: "measure_id",
        SemanticProposalKind.GRAIN: "grain_id",
    }
    identity_key = identity_defaults.get(proposal.kind)
    if identity_key is not None:
        payload.setdefault(identity_key, proposal.target_id)
    if proposal.kind is SemanticProposalKind.ALIAS:
        field_id = payload.get("field_id")
        if not isinstance(field_id, str) or field_id not in field_scopes:
            raise ValueError("alias proposals require an unambiguous field entity scope")
        payload["entity_id"] = field_scopes[field_id]
    elif proposal.kind in (
        SemanticProposalKind.CLASSIFICATION,
        SemanticProposalKind.IDENTIFIER,
    ):
        payload["policy_id"] = f"{proposal.kind.value}-{proposal.target_id}"
    return payload


def adapt_approved_proposals(
    proposal_set: SemanticProposalSet,
    *,
    descriptor_id: str,
) -> tuple[SemanticAssertion, ...]:
    """Convert approved proposals to pending assertions in stable ID order."""
    field_scopes = _field_scopes(proposal_set)
    assertions = [
        SemanticAssertion.create(
            type=_PROPOSAL_ASSERTION_TYPES[proposal.kind],
            payload=_assertion_payload(
                proposal,
                descriptor_id=descriptor_id,
                field_scopes=field_scopes,
            ),
            provenance=_provenance(proposal),
        )
        for proposal in proposal_set.proposals
        if proposal.status is ProposalStatus.APPROVED
    ]
    ids = [assertion.id for assertion in assertions]
    if len(ids) != len(set(ids)):
        raise ValueError("approved proposals produce duplicate assertion identities")
    return tuple(sorted(assertions, key=lambda assertion: assertion.id))


def create_manual_draft(
    *,
    draft_id: str,
    bundle_id: str,
    source_id: str,
    model_version: str,
    author_reference: str,
    assertions: Iterable[SemanticAssertion],
    deployment_bindings: Iterable[DeploymentBinding] = (),
) -> AssemblyDraft:
    """Create a draft from assertion-shaped manual bundle-as-code input."""
    materialized = tuple(assertions)
    if any(
        assertion.provenance.kind is not AssertionProvenanceKind.MANUAL
        for assertion in materialized
    ):
        raise ValueError("manual assembly input must carry manual provenance")
    return AssemblyDraft(
        apiVersion=ASSEMBLY_API_VERSION,
        draft_id=draft_id,
        bundle_id=bundle_id,
        source_id=source_id,
        model_version=model_version,
        assertions=materialized,
        deployment_bindings=tuple(deployment_bindings),
        author_reference=author_reference,
    )


def create_discovery_draft(
    proposal_set: SemanticProposalSet,
    *,
    descriptor_id: str,
    draft_id: str,
    bundle_id: str,
    source_id: str,
    model_version: str,
    author_reference: str,
    deployment_bindings: Iterable[DeploymentBinding] = (),
    expected_snapshot_fingerprint: str | None = None,
) -> AssemblyDraft:
    """Create the same draft shape from approved discovery proposals."""
    if (
        expected_snapshot_fingerprint is not None
        and proposal_set.snapshot_fingerprint != expected_snapshot_fingerprint
    ):
        raise ValueError("proposal set snapshot fingerprint is stale")
    return AssemblyDraft(
        apiVersion=ASSEMBLY_API_VERSION,
        draft_id=draft_id,
        bundle_id=bundle_id,
        source_id=source_id,
        model_version=model_version,
        assertions=adapt_approved_proposals(
            proposal_set,
            descriptor_id=descriptor_id,
        ),
        deployment_bindings=tuple(deployment_bindings),
        author_reference=author_reference,
        source_snapshot_fingerprint=proposal_set.snapshot_fingerprint,
    )