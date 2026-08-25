"""Immutable backend-neutral metadata snapshot contract models.

A ``MetadataSnapshot`` is the canonical safe result of metadata discovery:
authorized object/field names only, normalized type names, bounded
constraints, protected statistics, source/catalog fingerprints, freshness,
and provenance.  Raw values, credentials, connection material, native
driver objects, and unapproved identity data are structurally impossible
in these models - every text field is bounded and validated, every
collection is bounded, and fingerprints are computed over canonical
payloads so equivalent snapshots with different mapping insertion orders
produce identical identities.

Every fact carries a trust level: ``declared`` (authoritative source or
human), ``observed`` (directly observed metadata), or ``inferred``
(analysis suggestion).  Inferred facts never grant View visibility,
tenant access, mandatory filters, or execution authorization on their own.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nl2data_core.canonical import canonical_json, sha256_fingerprint

#: The only snapshot schema version the reference contract supports.
METADATA_SCHEMA_VERSION: Literal[1] = 1

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

#: Bounded collection and text limits for every snapshot model.
_MAX_OBJECTS = 1_024
_MAX_FIELDS = 16_384
_MAX_CONSTRAINTS = 4_096
_MAX_RELATIONSHIPS = 4_096
_MAX_STATISTICS = 8_192
_MAX_EVIDENCE = 4_096
_MAX_DESCRIPTION_CHARS = 1_024
_MAX_METHOD_CHARS = 128
_MAX_TYPE_CHARS = 64
_MAX_PATH_CHARS = 256


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MetadataTrustLevel(StrEnum):
    """How a metadata fact entered the snapshot.

    ``DECLARED`` facts are supplied by an authoritative source or a human;
    ``OBSERVED`` facts were directly observed from the source; ``INFERRED``
    facts are analysis suggestions.  Inferred facts remain non-authoritative
    until explicitly approved and can never grant access on their own.
    """

    DECLARED = "declared"
    OBSERVED = "observed"
    INFERRED = "inferred"


class MetadataObjectKind(StrEnum):
    """Backend-neutral object kinds a snapshot may carry."""

    TABLE = "table"
    VIEW = "view"
    COLLECTION = "collection"


class MetadataConstraintKind(StrEnum):
    """Bounded constraint kinds normalized from backend metadata."""

    PRIMARY_KEY = "primary_key"
    FOREIGN_KEY = "foreign_key"
    UNIQUE = "unique"
    NOT_NULL = "not_null"


class MetadataRelationshipKind(StrEnum):
    """Bounded relationship kinds between snapshot objects."""

    FOREIGN_KEY = "foreign_key"
    EMBEDDED = "embedded"


class MetadataStatisticKind(StrEnum):
    """Protected statistics a snapshot may carry.

    Only aggregate, value-free statistics are allowed; raw rows, documents,
    and unrestricted samples never cross the discovery boundary.
    """

    ROW_COUNT = "row_count"
    DISTINCT_ESTIMATE = "distinct_estimate"
    NULL_FRACTION = "null_fraction"
    MAX_LENGTH = "max_length"


class MetadataEvidence(BaseModel):
    """One bounded evidence reference behind an observed or inferred fact.

    ``reference`` is an opaque fingerprint or bounded identifier - never a
    raw value, document, row, credential, or physical source detail.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    kind: str = Field(min_length=1, max_length=32)
    reference: str = Field(pattern=_FINGERPRINT_PATTERN)
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)

    @field_validator("description")
    @classmethod
    def _safe_description(cls, value: str) -> str:
        from nl2data_core.views.models import validate_safe_description

        return validate_safe_description(value)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "reference": self.reference,
            "description": self.description,
        }


class MetadataConfidence(BaseModel):
    """Bounded confidence of an inferred fact.

    ``value`` is a closed 0..1 fraction, ``method`` names the deterministic
    analysis that produced the fact, and ``evidence_ids`` reference the
    bounded evidence records backing it.  Confidence is metadata, never
    authorization.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float = Field(ge=0.0, le=1.0)
    method: str = Field(min_length=1, max_length=_MAX_METHOD_CHARS)
    evidence_ids: frozenset[str] = Field(default_factory=frozenset, max_length=64)

    @field_validator("evidence_ids")
    @classmethod
    def _valid_evidence_ids(cls, value: frozenset[str]) -> frozenset[str]:
        for evidence_id in value:
            if re.fullmatch(_IDENTIFIER_PATTERN, evidence_id) is None:
                raise ValueError("evidence references must be bounded identifiers")
        return value

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "method": self.method,
            "evidence_ids": sorted(self.evidence_ids),
        }


class MetadataField(BaseModel):
    """One normalized field of a snapshot object.

    ``path`` is the physical field reference (a SQL column name or a
    canonical MongoDB dotted path); ``data_type`` is the normalized
    backend-neutral type name.  Only names and normalized types are
    carried - values are never sampled into a snapshot.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    object_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    path: str = Field(min_length=1, max_length=_MAX_PATH_CHARS)
    data_type: str = Field(min_length=1, max_length=_MAX_TYPE_CHARS)
    nullable: bool = True
    trust_level: MetadataTrustLevel = MetadataTrustLevel.OBSERVED
    confidence: MetadataConfidence | None = None
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)

    @field_validator("description")
    @classmethod
    def _safe_description(cls, value: str) -> str:
        from nl2data_core.views.models import validate_safe_description

        return validate_safe_description(value)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "object_id": self.object_id,
            "path": self.path,
            "data_type": self.data_type,
            "nullable": self.nullable,
            "trust_level": self.trust_level.value,
            "confidence": (
                self.confidence.canonical_payload() if self.confidence is not None else None
            ),
            "description": self.description,
        }


class MetadataConstraint(BaseModel):
    """One bounded constraint over snapshot fields.

    ``fields`` are the constrained field ids; foreign keys additionally
    reference their target through a snapshot relationship, so the
    constraint itself stays value-free.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    constraint_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    kind: MetadataConstraintKind
    fields: frozenset[str] = Field(default_factory=frozenset, max_length=_MAX_FIELDS)
    trust_level: MetadataTrustLevel = MetadataTrustLevel.OBSERVED
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)

    @field_validator("fields")
    @classmethod
    def _valid_fields(cls, value: frozenset[str]) -> frozenset[str]:
        for field in value:
            if re.fullmatch(_IDENTIFIER_PATTERN, field) is None:
                raise ValueError("constraint fields must be bounded identifiers")
        return value

    @field_validator("description")
    @classmethod
    def _safe_description(cls, value: str) -> str:
        from nl2data_core.views.models import validate_safe_description

        return validate_safe_description(value)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "constraint_id": self.constraint_id,
            "kind": self.kind.value,
            "fields": sorted(self.fields),
            "trust_level": self.trust_level.value,
            "description": self.description,
        }


class MetadataStatistic(BaseModel):
    """One protected aggregate statistic.

    ``value`` is a bounded float only; raw values, rows, and documents
    never appear.  ``scope_object_id``/``scope_field_id`` name the scope
    of the statistic (object-level or field-level).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    statistic_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    kind: MetadataStatisticKind
    scope_object_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    scope_field_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    value: float | None = Field(default=None, ge=0.0)
    trust_level: MetadataTrustLevel = MetadataTrustLevel.OBSERVED

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "statistic_id": self.statistic_id,
            "kind": self.kind.value,
            "scope_object_id": self.scope_object_id,
            "scope_field_id": self.scope_field_id,
            "value": self.value,
            "trust_level": self.trust_level.value,
        }


class MetadataRelationship(BaseModel):
    """One bounded relationship between two snapshot objects.

    ``source_fields`` and ``target_fields`` carry only field ids - the
    relationship never carries values, keys, or raw material.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    relationship_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    kind: MetadataRelationshipKind
    source_object_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    target_object_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    source_fields: frozenset[str] = Field(default_factory=frozenset, max_length=_MAX_FIELDS)
    target_fields: frozenset[str] = Field(default_factory=frozenset, max_length=_MAX_FIELDS)
    trust_level: MetadataTrustLevel = MetadataTrustLevel.OBSERVED
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)

    @field_validator("source_fields", "target_fields")
    @classmethod
    def _valid_fields(cls, value: frozenset[str]) -> frozenset[str]:
        for field in value:
            if re.fullmatch(_IDENTIFIER_PATTERN, field) is None:
                raise ValueError("relationship fields must be bounded identifiers")
        return value

    @field_validator("description")
    @classmethod
    def _safe_description(cls, value: str) -> str:
        from nl2data_core.views.models import validate_safe_description

        return validate_safe_description(value)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "relationship_id": self.relationship_id,
            "kind": self.kind.value,
            "source_object_id": self.source_object_id,
            "target_object_id": self.target_object_id,
            "source_fields": sorted(self.source_fields),
            "target_fields": sorted(self.target_fields),
            "trust_level": self.trust_level.value,
            "description": self.description,
        }


class MetadataObject(BaseModel):
    """One discovered object: table, view, or collection.

    ``observed_incomplete`` marks dynamic sources (for example MongoDB
    dotted paths sampled from one bounded document) where the observation
    is not a complete schema declaration.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    object_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    kind: MetadataObjectKind
    name: str = Field(min_length=1, max_length=_MAX_PATH_CHARS)
    fields: tuple[MetadataField, ...] = Field(default_factory=tuple, max_length=_MAX_FIELDS)
    constraints: tuple[MetadataConstraint, ...] = Field(
        default_factory=tuple, max_length=_MAX_CONSTRAINTS
    )
    statistics: tuple[MetadataStatistic, ...] = Field(
        default_factory=tuple, max_length=_MAX_STATISTICS
    )
    trust_level: MetadataTrustLevel = MetadataTrustLevel.OBSERVED
    observed_incomplete: bool = False

    @field_validator("fields")
    @classmethod
    def _unique_fields(cls, value: tuple[MetadataField, ...]) -> tuple[MetadataField, ...]:
        ids = [field.field_id for field in value]
        if len(ids) != len(set(ids)):
            raise ValueError("object field ids must be unique")
        paths = [field.path for field in value]
        if len(paths) != len(set(paths)):
            raise ValueError("object field paths must be unique")
        return value

    @field_validator("constraints")
    @classmethod
    def _unique_constraints(
        cls, value: tuple[MetadataConstraint, ...]
    ) -> tuple[MetadataConstraint, ...]:
        ids = [constraint.constraint_id for constraint in value]
        if len(ids) != len(set(ids)):
            raise ValueError("object constraint ids must be unique")
        return value

    @field_validator("statistics")
    @classmethod
    def _unique_statistics(
        cls, value: tuple[MetadataStatistic, ...]
    ) -> tuple[MetadataStatistic, ...]:
        ids = [statistic.statistic_id for statistic in value]
        if len(ids) != len(set(ids)):
            raise ValueError("object statistic ids must be unique")
        return value

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "kind": self.kind.value,
            "name": self.name,
            "fields": [
                field.canonical_payload()
                for field in sorted(self.fields, key=lambda item: item.field_id)
            ],
            "constraints": [
                    constraint.canonical_payload()
                    for constraint in sorted(
                        self.constraints, key=lambda item: item.constraint_id
                    )
            ],
            "statistics": [
                    statistic.canonical_payload()
                    for statistic in sorted(
                        self.statistics, key=lambda item: item.statistic_id
                    )
            ],
            "trust_level": self.trust_level.value,
            "observed_incomplete": self.observed_incomplete,
        }

    def field_ids(self) -> frozenset[str]:
        """Every field id declared on this object."""
        return frozenset(field.field_id for field in self.fields)


class MetadataFreshness(BaseModel):
    """Freshness and sampling bounds of one snapshot.

    The boolean flags record when discovery truncated at a configured
    limit, so consumers can tell complete observations from bounded ones.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    discovered_at: datetime = Field(default_factory=_utc_now)
    bounded_objects: bool = False
    bounded_fields: bool = False
    bounded_samples: bool = False
    sample_limit: int | None = Field(default=None, ge=1, le=1_000_000)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "discovered_at": self.discovered_at.isoformat(),
            "bounded_objects": self.bounded_objects,
            "bounded_fields": self.bounded_fields,
            "bounded_samples": self.bounded_samples,
            "sample_limit": self.sample_limit,
        }


class MetadataSourceReference(BaseModel):
    """Safe identity of the source a snapshot describes.

    ``source_id`` is the logical source name and ``catalog_fingerprint``
    is the stable catalog reference of this snapshot's source - never
    credentials, DSNs, or physical connection details.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    catalog_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)

    @field_validator("description")
    @classmethod
    def _safe_description(cls, value: str) -> str:
        from nl2data_core.views.models import validate_safe_description

        return validate_safe_description(value)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "catalog_fingerprint": self.catalog_fingerprint,
            "description": self.description,
        }


class MetadataProvenance(BaseModel):
    """Safe provenance of one snapshot.

    ``discovered_by_fingerprint`` references the discoverer identity
    without raw host details; ``method`` names the discovery method;
    ``evidence`` carries bounded opaque references only.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    discovered_by_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    discovered_at: datetime = Field(default_factory=_utc_now)
    method: str = Field(min_length=1, max_length=_MAX_METHOD_CHARS)
    evidence: tuple[MetadataEvidence, ...] = Field(
        default_factory=tuple, max_length=_MAX_EVIDENCE
    )

    @field_validator("evidence")
    @classmethod
    def _unique_evidence(
        cls, value: tuple[MetadataEvidence, ...]
    ) -> tuple[MetadataEvidence, ...]:
        ids = [evidence.evidence_id for evidence in value]
        if len(ids) != len(set(ids)):
            raise ValueError("provenance evidence ids must be unique")
        return value

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "discovered_by_fingerprint": self.discovered_by_fingerprint,
            "discovered_at": self.discovered_at.isoformat(),
            "method": self.method,
            "evidence": [
                evidence.canonical_payload()
                for evidence in sorted(self.evidence, key=lambda item: item.evidence_id)
            ],
        }


class MetadataSnapshot(BaseModel):
    """An immutable, versioned, canonical metadata snapshot.

    The snapshot is the common safe result of metadata discovery: it
    carries only authorized structural metadata, normalized types and
    constraints, bounded protected statistics, source/catalog identity,
    freshness, and safe provenance.  The fingerprint is canonical -
    equivalent snapshots with different mapping insertion orders produce
    the same identity - and once constructed a snapshot can never be
    mutated; a new observation is a new snapshot with a new fingerprint.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = METADATA_SCHEMA_VERSION
    snapshot_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    source: MetadataSourceReference
    objects: tuple[MetadataObject, ...] = Field(default_factory=tuple, max_length=_MAX_OBJECTS)
    relationships: tuple[MetadataRelationship, ...] = Field(
        default_factory=tuple, max_length=_MAX_RELATIONSHIPS
    )
    freshness: MetadataFreshness = Field(default_factory=MetadataFreshness)
    provenance: MetadataProvenance
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @field_validator("objects")
    @classmethod
    def _unique_objects(cls, value: tuple[MetadataObject, ...]) -> tuple[MetadataObject, ...]:
        ids = [obj.object_id for obj in value]
        if len(ids) != len(set(ids)):
            raise ValueError("snapshot object ids must be unique")
        return value

    @field_validator("relationships")
    @classmethod
    def _valid_relationships(
        cls, value: tuple[MetadataRelationship, ...]
    ) -> tuple[MetadataRelationship, ...]:
        ids = [relationship.relationship_id for relationship in value]
        if len(ids) != len(set(ids)):
            raise ValueError("snapshot relationship ids must be unique")
        return value

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> MetadataSnapshot:
        fingerprint = sha256_fingerprint(self.canonical_payload())
        object.__setattr__(self, "fingerprint", fingerprint)
        return self

    def canonical_payload(self) -> dict[str, Any]:
        """The canonical payload the fingerprint is derived from.

        Objects and relationships are sorted by id so the same metadata
        mapped in a different backend iteration order produces the same
        fingerprint; field order within an object stays structural.
        """
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "source": self.source.canonical_payload(),
            "objects": [
                obj.canonical_payload()
                for obj in sorted(self.objects, key=lambda item: item.object_id)
            ],
            "relationships": [
                relationship.canonical_payload()
                for relationship in sorted(
                    self.relationships, key=lambda item: item.relationship_id
                )
            ],
            "freshness": self.freshness.canonical_payload(),
            "provenance": self.provenance.canonical_payload(),
        }

    def serialize_canonical(self) -> str:
        """Canonical JSON with explicit schema version and sorted keys."""
        return canonical_json(self.canonical_payload())

    def safe_payload(self) -> dict[str, Any]:
        """Serialize with safe references and fingerprints only.

        The snapshot is safe by construction - canonical payload carries
        no raw values, credentials, native objects, or unapproved identity
        data - and the fingerprint itself is included for evidence.
        """
        payload = self.canonical_payload()
        payload["fingerprint"] = self.fingerprint
        return payload

    def object(self, object_id: str) -> MetadataObject | None:
        """The object with the given id, or ``None`` when absent."""
        for obj in self.objects:
            if obj.object_id == object_id:
                return obj
        return None

    def object_ids(self) -> frozenset[str]:
        """Every object id declared in the snapshot."""
        return frozenset(obj.object_id for obj in self.objects)

    def field(self, field_id: str) -> MetadataField | None:
        """The first field with the given id, or ``None`` when absent."""
        for obj in self.objects:
            for field in obj.fields:
                if field.field_id == field_id:
                    return field
        return None

    def field_ids(self) -> frozenset[str]:
        """Every field id declared anywhere in the snapshot."""
        return frozenset(
            field.field_id for obj in self.objects for field in obj.fields
        )

    def field_path(self, field_id: str) -> str | None:
        """The physical path of a field id, or ``None`` when absent."""
        field = self.field(field_id)
        return field.path if field is not None else None
