"""Deterministic semantic inference over metadata snapshots.

The first inference engine uses deterministic rules only: object/field
mapping, type normalization, identifier/name patterns, native constraints,
and bounded path observation.  Every generated proposal carries its method,
confidence, evidence fingerprint, trust level, freshness, and source
snapshot identity, and stays non-authoritative until a reviewer approves
it.  LLM assistance is deferred and, if added later, must produce the same
proposal contract and remain untrusted until review.

Inference never samples raw values and never grants access: proposals are
metadata, and only the View/governance resolution boundary grants
visibility or execution authority.
"""

from __future__ import annotations

import re

from nl2data_core.canonical import strict_sha256_fingerprint

from .models import (
    MetadataConfidence,
    MetadataEvidence,
    MetadataSnapshot,
    MetadataTrustLevel,
)
from .proposals import (
    SemanticProposal,
    SemanticProposalKind,
    SemanticProposalSet,
)

#: Bounded default proposal cap for one inference pass.
_DEFAULT_MAX_PROPOSALS = 4_096

_IDENTIFIER_RE = re.compile(r"^(id|uuid|guid)$", re.IGNORECASE)
_REFERENCE_RE = re.compile(r"^(.*)_(id|key|uuid|guid)$", re.IGNORECASE)
_EMAIL_RE = re.compile(r"email|e_?mail", re.IGNORECASE)
_PHONE_RE = re.compile(r"phone|mobile|telephone|cell", re.IGNORECASE)
_NAME_RE = re.compile(r"(^|_)(name|fullname|full_name)(_|$)", re.IGNORECASE)
_ADDRESS_RE = re.compile(r"address|city|country|region|postal", re.IGNORECASE)
_PERSONAL_RE = re.compile(r"ssn|passport|national_id|driver_license", re.IGNORECASE)


def _humanize(name: str) -> str:
    """Bounded human-readable label from a snake/dotted name."""
    return name.replace(".", " ").replace("_", " ").replace("-", " ").strip().title()


def _normalized_type(data_type: str) -> str:
    """Map a backend-normalized type name to a canonical semantic type."""
    lowered = data_type.lower()
    if any(
        token in lowered
        for token in ("int", "serial", "numeric", "decimal", "float", "real", "double")
    ):
        if any(token in lowered for token in ("float", "real", "double", "decimal", "numeric")):
            return "float"
        return "integer"
    if any(token in lowered for token in ("bool",)):
        return "boolean"
    if any(token in lowered for token in ("date", "time")):
        return "date" if "time" not in lowered else "timestamp"
    if any(token in lowered for token in ("timestamp",)):
        return "timestamp"
    if any(token in lowered for token in ("text", "char", "clob", "string")):
        return "text"
    if any(token in lowered for token in ("blob", "binary", "bytea")):
        return "blob"
    if any(token in lowered for token in ("json", "document", "object", "array")):
        return "document"
    return "unknown"


def _evidence_reference(evidence: MetadataEvidence) -> str:
    """Canonical fingerprint of one evidence record."""
    return strict_sha256_fingerprint(evidence.canonical_payload())


class _InferenceBuilder:
    """Accumulates bounded proposals and evidence for one snapshot."""

    def __init__(self, snapshot: MetadataSnapshot, *, max_proposals: int) -> None:
        self._snapshot = snapshot
        self._max_proposals = max_proposals
        self._proposals: list[SemanticProposal] = []
        self._evidence: dict[str, MetadataEvidence] = {}

    @property
    def full(self) -> bool:
        return len(self._proposals) >= self._max_proposals

    def _add_evidence(self, evidence: MetadataEvidence) -> None:
        self._evidence.setdefault(evidence.evidence_id, evidence)

    def _confidence(
        self, value: float, method: str, evidence_ids: tuple[str, ...]
    ) -> MetadataConfidence:
        return MetadataConfidence(
            value=value, method=method, evidence_ids=frozenset(evidence_ids)
        )

    def _evidence_fingerprint(self, evidence_ids: tuple[str, ...]) -> str:
        references = sorted(
            self._evidence[evidence_id].reference for evidence_id in evidence_ids
        )
        return strict_sha256_fingerprint({"evidence": references})

    def _emit(
        self,
        *,
        kind: SemanticProposalKind,
        target_id: str,
        fact: dict[str, object],
        method: str,
        confidence: float,
        evidence_ids: tuple[str, ...],
        trust_level: MetadataTrustLevel = MetadataTrustLevel.INFERRED,
    ) -> None:
        if self.full:
            return
        evidence_fingerprint = self._evidence_fingerprint(evidence_ids)
        self._proposals.append(
            SemanticProposal(
                proposal_id=f"{kind.value}-{target_id}-{len(self._proposals) + 1}",
                kind=kind,
                target_id=target_id,
                fact=fact,
                trust_level=trust_level,
                method=method,
                confidence=self._confidence(confidence, method, evidence_ids),
                evidence_fingerprint=evidence_fingerprint,
                snapshot_fingerprint=self._snapshot.fingerprint,
            )
        )


def _object_evidence(snapshot: MetadataSnapshot, object_id: str) -> MetadataEvidence:
    obj = snapshot.object(object_id)
    reference = (
        strict_sha256_fingerprint(obj.canonical_payload())
        if obj is not None
        else strict_sha256_fingerprint({})
    )
    return MetadataEvidence(
        evidence_id=f"obj-{object_id}",
        kind="object",
        reference=reference,
        description="object structure observation",
    )


def _field_evidence(snapshot: MetadataSnapshot, object_id: str, field_id: str) -> MetadataEvidence:
    field = snapshot.field(field_id)
    reference = (
        strict_sha256_fingerprint(field.canonical_payload())
        if field is not None
        else strict_sha256_fingerprint({})
    )
    return MetadataEvidence(
        evidence_id=f"fld-{object_id}-{field_id}",
        kind="field",
        reference=reference,
        description="field structure observation",
    )


def infer_proposals(
    snapshot: MetadataSnapshot,
    *,
    max_proposals: int = _DEFAULT_MAX_PROPOSALS,
) -> SemanticProposalSet:
    """Generate bounded deterministic semantic proposals for a snapshot.

    Proposals cover entities, fields, identifiers, relationships, grains,
    measures, aliases, and classifications.  Every proposal is generated
    from structural metadata only (never raw values) and remains PENDING
    until explicitly approved.
    """
    builder = _InferenceBuilder(snapshot, max_proposals=max_proposals)

    # -- entities and fields (observed structure) ---------------------------
    for obj in snapshot.objects:
        if builder.full:
            break
        object_evidence = _object_evidence(snapshot, obj.object_id)
        builder._add_evidence(object_evidence)
        builder._emit(
            kind=SemanticProposalKind.ENTITY,
            target_id=obj.object_id,
            fact={
                "entity_id": obj.object_id,
                "label": _humanize(obj.name),
                "object_id": obj.object_id,
                "object_kind": obj.kind.value,
            },
            method="object_mapping",
            confidence=1.0,
            evidence_ids=(object_evidence.evidence_id,),
            trust_level=MetadataTrustLevel.OBSERVED,
        )
        for field in obj.fields:
            if builder.full:
                break
            field_evidence = _field_evidence(snapshot, obj.object_id, field.field_id)
            builder._add_evidence(field_evidence)
            builder._emit(
                kind=SemanticProposalKind.FIELD,
                target_id=field.field_id,
                fact={
                    "entity_id": obj.object_id,
                    "field_id": field.field_id,
                    "path": field.path,
                    "data_type": _normalized_type(field.data_type),
                    "nullable": field.nullable,
                    "label": _humanize(field.path),
                },
                method="field_mapping",
                confidence=1.0,
                evidence_ids=(field_evidence.evidence_id,),
                trust_level=MetadataTrustLevel.OBSERVED,
            )

    # -- identifiers (name patterns) ----------------------------------------
    for obj in snapshot.objects:
        if builder.full:
            break
        for field in obj.fields:
            if builder.full:
                break
            field_evidence = _field_evidence(snapshot, obj.object_id, field.field_id)
            builder._add_evidence(field_evidence)
            if _IDENTIFIER_RE.fullmatch(field.path):
                builder._emit(
                    kind=SemanticProposalKind.IDENTIFIER,
                    target_id=field.field_id,
                    fact={"field_id": field.field_id, "role": "primary"},
                    method="identifier_pattern",
                    confidence=0.95,
                    evidence_ids=(field_evidence.evidence_id,),
                )
            else:
                match = _REFERENCE_RE.fullmatch(field.path)
                if match is not None:
                    builder._emit(
                        kind=SemanticProposalKind.IDENTIFIER,
                        target_id=field.field_id,
                        fact={"field_id": field.field_id, "role": "reference"},
                        method="identifier_pattern",
                        confidence=0.85,
                        evidence_ids=(field_evidence.evidence_id,),
                    )

    # -- relationships (constraints first, then name patterns) --------------
    for relationship in snapshot.relationships:
        if builder.full:
            break
        evidence = MetadataEvidence(
            evidence_id=f"rel-{relationship.relationship_id}",
            kind="relationship",
            reference=strict_sha256_fingerprint(
                {
                    "source_object_id": relationship.source_object_id,
                    "target_object_id": relationship.target_object_id,
                    "source_fields": sorted(relationship.source_fields),
                    "target_fields": sorted(relationship.target_fields),
                }
            ),
            description="relationship observation",
        )
        builder._add_evidence(evidence)
        builder._emit(
            kind=SemanticProposalKind.RELATIONSHIP,
            target_id=relationship.relationship_id,
            fact={
                "relationship_id": relationship.relationship_id,
                "source_entity_id": relationship.source_object_id,
                "target_entity_id": relationship.target_object_id,
                "source_fields": sorted(relationship.source_fields),
                "target_fields": sorted(relationship.target_fields),
                "label": _humanize(relationship.relationship_id),
            },
            method="foreign_key",
            confidence=1.0,
            evidence_ids=(evidence.evidence_id,),
            trust_level=MetadataTrustLevel.OBSERVED,
        )

    object_ids = snapshot.object_ids()
    for obj in snapshot.objects:
        if builder.full:
            break
        for field in obj.fields:
            if builder.full:
                break
            match = _REFERENCE_RE.fullmatch(field.path)
            if match is None:
                continue
            referenced = match.group(1).lower()
            if referenced not in object_ids:
                continue
            field_evidence = _field_evidence(snapshot, obj.object_id, field.field_id)
            builder._add_evidence(field_evidence)
            relationship_id = f"{obj.object_id}_{referenced}_via_{field.field_id}"
            builder._emit(
                kind=SemanticProposalKind.RELATIONSHIP,
                target_id=relationship_id,
                fact={
                    "relationship_id": relationship_id,
                    "source_entity_id": obj.object_id,
                    "target_entity_id": referenced,
                    "source_fields": (field.field_id,),
                    "target_fields": (),
                    "label": _humanize(f"{referenced} {field.path}"),
                },
                method="name_pattern",
                confidence=0.6,
                evidence_ids=(field_evidence.evidence_id,),
            )

    # -- grains and measures -------------------------------------------------
    for obj in snapshot.objects:
        if builder.full:
            break
        identifier_fields = tuple(
            field.field_id
            for field in obj.fields
            if _IDENTIFIER_RE.fullmatch(field.path)
            or _REFERENCE_RE.fullmatch(field.path)
        )
        if identifier_fields:
            object_evidence = _object_evidence(snapshot, obj.object_id)
            builder._add_evidence(object_evidence)
            builder._emit(
                kind=SemanticProposalKind.GRAIN,
                target_id=f"{obj.object_id}_grain",
                fact={
                    "grain_id": f"{obj.object_id}_grain",
                    "entity_id": obj.object_id,
                    "attributes": identifier_fields,
                },
                method="identifier_grain",
                confidence=0.8,
                evidence_ids=(object_evidence.evidence_id,),
            )
        for field in obj.fields:
            if builder.full:
                break
            normalized = _normalized_type(field.data_type)
            if normalized not in {"integer", "float"}:
                continue
            field_evidence = _field_evidence(snapshot, obj.object_id, field.field_id)
            builder._add_evidence(field_evidence)
            for aggregation in ("sum", "avg", "min", "max"):
                if builder.full:
                    break
                builder._emit(
                    kind=SemanticProposalKind.MEASURE,
                    target_id=f"{field.field_id}_{aggregation}",
                    fact={
                        "measure_id": f"{field.field_id}_{aggregation}",
                        "field_id": field.field_id,
                        "aggregation": aggregation,
                        "label": _humanize(f"{field.path} {aggregation}"),
                    },
                    method="numeric_measure",
                    confidence=0.75,
                    evidence_ids=(field_evidence.evidence_id,),
                )

    # -- aliases and classifications (name patterns) -------------------------
    for obj in snapshot.objects:
        if builder.full:
            break
        for field in obj.fields:
            if builder.full:
                break
            field_evidence = _field_evidence(snapshot, obj.object_id, field.field_id)
            builder._add_evidence(field_evidence)
            humanized = _humanize(field.path)
            if humanized.lower() != field.path.lower():
                builder._emit(
                    kind=SemanticProposalKind.ALIAS,
                    target_id=field.field_id,
                    fact={"field_id": field.field_id, "alias": humanized},
                    method="name_humanization",
                    confidence=0.7,
                    evidence_ids=(field_evidence.evidence_id,),
                )
            classification: str | None = None
            if _EMAIL_RE.search(field.path):
                classification = "email_contact"
            elif _PHONE_RE.search(field.path):
                classification = "phone_contact"
            elif _NAME_RE.search(field.path):
                classification = "person_name"
            elif _ADDRESS_RE.search(field.path):
                classification = "location"
            elif _PERSONAL_RE.search(field.path):
                classification = "personal_identifier"
            if classification is not None:
                builder._emit(
                    kind=SemanticProposalKind.CLASSIFICATION,
                    target_id=field.field_id,
                    fact={"field_id": field.field_id, "classification": classification},
                    method="classification_pattern",
                    confidence=0.8,
                    evidence_ids=(field_evidence.evidence_id,),
                )

    return SemanticProposalSet(
        snapshot_fingerprint=snapshot.fingerprint,
        proposals=tuple(builder._proposals),
    )
