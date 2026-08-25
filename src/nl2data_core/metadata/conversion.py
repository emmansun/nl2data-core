"""Conversion of approved semantic proposals into Semantic Model Bundle inputs.

Only explicitly approved proposals convert; PENDING, REJECTED, and REVISED
proposals never become bundle inputs.  Conversion preserves provenance and
trust markers: every converted proposal is recorded as an opaque
:class:`ProposalReference` (fingerprint references only) and every
converted field/relationship fact carries an approved
``SemanticTrustMarker``.  Incompatible proposals (for example a field whose
entity was not approved) are skipped so the produced input stays valid and
compatible with the source snapshot.

Conversion produces bundle *inputs* (a descriptor plus measures, grains,
and markers) - it does not publish or activate anything, and approval never
grants View visibility or execution authority by itself.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nl2data_core.bundles.models import (
    SemanticGrain,
    SemanticMeasure,
    SemanticTrustKind,
    SemanticTrustMarker,
)
from nl2data_core.views.models import SemanticDescriptor

from .models import MetadataTrustLevel
from .proposals import ProposalStatus, SemanticProposal, SemanticProposalKind, SemanticProposalSet

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

#: Bounded conversion limits.
_MAX_REFERENCES = 16_384
_MAX_MARKERS = 4_096

#: Measure aggregations the bundle model accepts, keyed by canonical name.
_MEASURE_AGGREGATIONS: dict[str, Literal["none", "count", "sum", "avg", "min", "max"]] = {
    "none": "none",
    "count": "count",
    "sum": "sum",
    "avg": "avg",
    "min": "min",
    "max": "max",
}


class ProposalReference(BaseModel):
    """Opaque reference to one converted proposal.

    Carries only bounded identifiers and fingerprints - never the proposal
    payload, raw identities, or physical source details.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    kind: SemanticProposalKind
    target_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    evidence_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    trust_level: MetadataTrustLevel
    snapshot_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "proposal_id": self.proposal_id,
            "kind": self.kind.value,
            "target_id": self.target_id,
            "evidence_fingerprint": self.evidence_fingerprint,
            "trust_level": self.trust_level.value,
            "snapshot_fingerprint": self.snapshot_fingerprint,
        }


class ConvertedBundleInput(BaseModel):
    """Bundle inputs converted from an approved proposal set.

    The descriptor is the semantic core; measures, grains, and trust
    markers carry the approved semantics, and ``proposal_references``
    preserve provenance so a published bundle stays traceable to its
    discovery source snapshot.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    descriptor: SemanticDescriptor
    measures: tuple[SemanticMeasure, ...] = Field(default_factory=tuple, max_length=4_096)
    grains: tuple[SemanticGrain, ...] = Field(default_factory=tuple, max_length=256)
    trust_markers: tuple[SemanticTrustMarker, ...] = Field(
        default_factory=tuple, max_length=_MAX_MARKERS
    )
    proposal_references: tuple[ProposalReference, ...] = Field(
        default_factory=tuple, max_length=_MAX_REFERENCES
    )
    source_snapshot_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)

    def safe_payload(self) -> dict[str, object]:
        """Serialize with fingerprints and bounded references only."""
        return {
            "descriptor": self.descriptor.canonical_payload(),
            "measures": [measure.canonical_payload() for measure in self.measures],
            "grains": [grain.canonical_payload() for grain in self.grains],
            "trust_markers": [marker.canonical_payload() for marker in self.trust_markers],
            "proposal_references": [
                reference.canonical_payload() for reference in self.proposal_references
            ],
            "source_snapshot_fingerprint": self.source_snapshot_fingerprint,
        }


def _approved(proposal_set: SemanticProposalSet) -> tuple[SemanticProposal, ...]:
    return proposal_set.by_status(ProposalStatus.APPROVED)


def convert_approved_proposals(
    proposal_set: SemanticProposalSet,
    *,
    descriptor_id: str,
    version: int = 1,
    source_id: str,
) -> ConvertedBundleInput | None:
    """Convert the approved proposals of a set into bundle inputs.

    Returns ``None`` when no proposals are approved; otherwise returns a
    descriptor plus measures, grains, trust markers, and proposal
    references.  Unapproved proposals and incompatible references are
    never included, and the source snapshot fingerprint is preserved on
    every reference so stale evidence stays detectable.
    """
    approved = _approved(proposal_set)
    if not approved:
        return None

    by_kind: dict[SemanticProposalKind, list[SemanticProposal]] = {}
    for proposal in approved:
        by_kind.setdefault(proposal.kind, []).append(proposal)

    # -- entity and field facts ---------------------------------------------
    from nl2data_core.views.models import (
        SemanticEntityDescriptor,
        SemanticFieldDescriptor,
        SemanticRelationshipDescriptor,
    )

    entity_proposals = by_kind.get(SemanticProposalKind.ENTITY, [])
    field_proposals = by_kind.get(SemanticProposalKind.FIELD, [])
    relationship_proposals = by_kind.get(SemanticProposalKind.RELATIONSHIP, [])
    measure_proposals = by_kind.get(SemanticProposalKind.MEASURE, [])
    grain_proposals = by_kind.get(SemanticProposalKind.GRAIN, [])
    alias_proposals = by_kind.get(SemanticProposalKind.ALIAS, [])
    classification_proposals = by_kind.get(SemanticProposalKind.CLASSIFICATION, [])

    entity_ids = frozenset(
        str(proposal.fact.get("entity_id", proposal.target_id))
        for proposal in entity_proposals
    )
    field_by_id: dict[str, SemanticProposal] = {
        str(proposal.fact.get("field_id", proposal.target_id)): proposal
        for proposal in field_proposals
    }
    alias_by_field = {
        str(proposal.fact.get("field_id")): str(proposal.fact.get("alias", ""))
        for proposal in alias_proposals
        if str(proposal.fact.get("field_id")) in field_by_id
    }
    classification_by_field: dict[str, list[str]] = {}
    for proposal in classification_proposals:
        field_id = str(proposal.fact.get("field_id"))
        if field_id in field_by_id:
            classification_by_field.setdefault(field_id, []).append(
                str(proposal.fact.get("classification", "unknown"))
            )

    entities: list[SemanticEntityDescriptor] = []
    converted_field_ids: set[str] = set()
    for proposal in sorted(entity_proposals, key=lambda item: item.target_id):
        entity_id = str(proposal.fact.get("entity_id", proposal.target_id))
        fields: list[SemanticFieldDescriptor] = []
        for field_proposal in sorted(field_proposals, key=lambda item: item.target_id):
            field_entity = str(field_proposal.fact.get("entity_id"))
            if field_entity != entity_id:
                continue
            field_id = str(field_proposal.fact.get("field_id", field_proposal.target_id))
            data_type = str(field_proposal.fact.get("data_type", "unknown"))
            if re.fullmatch(_IDENTIFIER_PATTERN, data_type) is None:
                continue
            label = alias_by_field.get(field_id) or str(
                field_proposal.fact.get("label", field_id)
            )
            description = ""
            if field_id in classification_by_field:
                description = "classification: " + ", ".join(
                    classification_by_field[field_id]
                )
            fields.append(
                SemanticFieldDescriptor(
                    field_id=field_id,
                    label=label[:256],
                    description=description[:1024],
                    data_type=data_type,
                )
            )
            converted_field_ids.add(field_id)
        entities.append(
            SemanticEntityDescriptor(
                entity_id=entity_id,
                label=str(proposal.fact.get("label", entity_id))[:256],
                description="",
                fields=tuple(fields),
            )
        )

    relationships: list[SemanticRelationshipDescriptor] = []
    for proposal in sorted(relationship_proposals, key=lambda item: item.target_id):
        source = str(proposal.fact.get("source_entity_id"))
        target = str(proposal.fact.get("target_entity_id"))
        if source not in entity_ids or target not in entity_ids:
            continue
        relationships.append(
            SemanticRelationshipDescriptor(
                relationship_id=proposal.target_id,
                source_entity_id=source,
                target_entity_id=target,
                label=str(proposal.fact.get("label", proposal.target_id))[:256],
            )
        )

    descriptor = SemanticDescriptor(
        descriptor_id=descriptor_id,
        version=version,
        source_id=source_id,
        catalog_fingerprint=proposal_set.snapshot_fingerprint,
        entities=tuple(entities),
    )

    # -- measures and grains (approved, compatible only) ---------------------
    field_ids = descriptor.all_field_ids()
    measures = tuple(
        SemanticMeasure(
            measure_id=proposal.target_id,
            field_id=str(proposal.fact.get("field_id")),
            aggregation=aggregation,
            label=str(proposal.fact.get("label", proposal.target_id))[:256],
        )
        for proposal in sorted(measure_proposals, key=lambda item: item.target_id)
        if str(proposal.fact.get("field_id")) in field_ids
        if (aggregation := _MEASURE_AGGREGATIONS.get(
            str(proposal.fact.get("aggregation", "none"))
        ))
        is not None
    )
    entity_ids_in_descriptor = frozenset(
        entity.entity_id for entity in descriptor.entities
    )
    grains = tuple(
        SemanticGrain(
            grain_id=proposal.target_id,
            entity_id=str(proposal.fact.get("entity_id")),
            attributes=frozenset(
                attribute
                for attribute in proposal.fact.get("attributes", ())
                if attribute in field_ids
            ),
        )
        for proposal in sorted(grain_proposals, key=lambda item: item.target_id)
        if str(proposal.fact.get("entity_id")) in entity_ids_in_descriptor
        and proposal.fact.get("attributes")
    )

    # -- provenance: trust markers and proposal references -------------------
    fact_ids = descriptor.all_field_ids() | descriptor.all_relationship_ids()
    markers: list[SemanticTrustMarker] = []
    for index, proposal in enumerate(approved):
        if proposal.target_id not in fact_ids:
            continue
        markers.append(
            SemanticTrustMarker(
                marker_id=f"proposal-{index + 1}",
                fact_id=proposal.target_id,
                kind=SemanticTrustKind.APPROVED,
                approved=True,
                note=f"approved via {proposal.method}",
            )
        )
    references = tuple(
        ProposalReference(
            proposal_id=proposal.proposal_id,
            kind=proposal.kind,
            target_id=proposal.target_id,
            evidence_fingerprint=proposal.evidence_fingerprint,
            trust_level=proposal.trust_level,
            snapshot_fingerprint=proposal.snapshot_fingerprint,
        )
        for proposal in approved
    )

    return ConvertedBundleInput(
        descriptor=descriptor,
        measures=measures,
        grains=grains,
        trust_markers=tuple(markers),
        proposal_references=references,
        source_snapshot_fingerprint=proposal_set.snapshot_fingerprint,
    )
