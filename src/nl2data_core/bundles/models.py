"""Immutable versioned Semantic Model Bundle contract models.

A Semantic Model Bundle is the authoritative safe semantic artifact: it
wraps the existing bounded descriptor primitives (entities, fields,
relationships - already validated by the descriptor models) and adds
artifact lifecycle metadata: measures/aggregations, semantic grain,
source/catalog references, dependency fingerprints, authored/inferred/
approved trust markers, safe provenance, quality status, and compatibility
information.

A bundle contains only safe logical semantics: no credentials, connection
strings, raw executable SQL/MQL/code, native objects, physical bindings,
or authorization claims.  Every collection is bounded, every model is
frozen, and fingerprints are canonical so equivalent contents with
different mapping insertion orders produce identical identities.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nl2data_core.canonical import canonical_json, sha256_fingerprint
from nl2data_core.planning.models import AggregationKind
from nl2data_core.views.models import SemanticDescriptor, validate_safe_description

#: The only bundle schema version the reference loader supports.
BUNDLE_SCHEMA_VERSION: Literal[1] = 1

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

#: Bounded collection and text limits for every bundle model.
_MAX_MEASURES = 4_096
_MAX_GRAINS = 256
_MAX_SOURCES = 64
_MAX_DEPENDENCIES = 64
_MAX_TRUST_MARKERS = 4_096
_MAX_GRAIN_ATTRIBUTES = 4_096
_MAX_DESCRIPTION_CHARS = 1_024
_MAX_LABEL_CHARS = 256
_MAX_NOTE_CHARS = 1_024
_MAX_OWNER_REFERENCE_CHARS = 256


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SemanticTrustKind(StrEnum):
    """How a semantic fact entered the model.

    ``AUTHORED`` facts are written by the model owner; ``INFERRED`` facts
    are discovered by analysis and are not authoritative on their own;
    ``APPROVED`` facts carry a recorded human approval.  Inference is
    metadata, never authorization: only View/governance resolution grants
    visibility or execution authority.
    """

    AUTHORED = "authored"
    INFERRED = "inferred"
    APPROVED = "approved"


class BundleQualityStatus(StrEnum):
    """Publication quality status of a bundle.

    ``DRAFT`` bundles are never publishable; ``VALIDATED`` and ``APPROVED``
    bundles may be published and activated by a catalog.
    """

    DRAFT = "draft"
    VALIDATED = "validated"
    APPROVED = "approved"


class SemanticMeasure(BaseModel):
    """One bounded semantic measure/aggregation over a descriptor field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    measure_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    field_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    aggregation: AggregationKind = "none"
    label: str = Field(min_length=1, max_length=_MAX_LABEL_CHARS)
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)

    @field_validator("description")
    @classmethod
    def _safe_description(cls, value: str) -> str:
        return validate_safe_description(value)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "measure_id": self.measure_id,
            "field_id": self.field_id,
            "aggregation": self.aggregation,
            "label": self.label,
            "description": self.description,
        }


class SemanticGrain(BaseModel):
    """One bounded semantic grain: the attributes an entity is measured at."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    grain_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    entity_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    attributes: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_GRAIN_ATTRIBUTES
    )
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)

    @field_validator("attributes")
    @classmethod
    def _valid_attributes(cls, value: frozenset[str]) -> frozenset[str]:
        for attribute in value:
            if re.fullmatch(_IDENTIFIER_PATTERN, attribute) is None:
                raise ValueError("grain attributes must be bounded identifiers")
        return value

    @field_validator("description")
    @classmethod
    def _safe_description(cls, value: str) -> str:
        return validate_safe_description(value)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "grain_id": self.grain_id,
            "entity_id": self.entity_id,
            "attributes": sorted(self.attributes),
            "description": self.description,
        }


class SemanticSourceReference(BaseModel):
    """A safe reference to a source/catalog the model is bound to.

    The reference is opaque and bounded: it names the logical source and
    carries a catalog fingerprint - never raw identities, connection
    material, credentials, or physical source details.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    catalog_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)

    @field_validator("description")
    @classmethod
    def _safe_description(cls, value: str) -> str:
        return validate_safe_description(value)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "reference_id": self.reference_id,
            "source_id": self.source_id,
            "catalog_fingerprint": self.catalog_fingerprint,
            "description": self.description,
        }


class BundleDependency(BaseModel):
    """One required dependency on another published model artifact.

    The dependency is identified by bundle id, version, and a required
    fingerprint; a catalog rejects activation when the referenced
    artifact is unavailable or its fingerprint differs (fail closed,
    never silently downgraded).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dependency_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    bundle_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    version: str = Field(pattern=_IDENTIFIER_PATTERN)
    fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "dependency_id": self.dependency_id,
            "bundle_id": self.bundle_id,
            "version": self.version,
            "fingerprint": self.fingerprint,
        }


class SemanticTrustMarker(BaseModel):
    """Trust metadata for one semantic fact (relationship or description).

    ``kind`` records whether the fact was authored, inferred, or approved;
    ``approved`` records a human approval.  An inferred fact without
    approval may be retained as metadata but can never independently grant
    View visibility or execution authority.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    marker_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    fact_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    kind: SemanticTrustKind = SemanticTrustKind.AUTHORED
    approved: bool = False
    note: str = Field(default="", max_length=_MAX_NOTE_CHARS)

    @model_validator(mode="after")
    def _approval_consistency(self) -> SemanticTrustMarker:
        if self.kind is SemanticTrustKind.APPROVED and not self.approved:
            raise ValueError("approved trust markers require approved=True")
        if self.kind is SemanticTrustKind.AUTHORED and self.approved:
            raise ValueError("authored trust markers cannot carry a separate approval")
        return self

    @field_validator("note")
    @classmethod
    def _safe_note(cls, value: str) -> str:
        return validate_safe_description(value)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "marker_id": self.marker_id,
            "fact_id": self.fact_id,
            "kind": self.kind.value,
            "approved": self.approved,
            "note": self.note,
        }


class BundleProvenance(BaseModel):
    """Safe provenance of a bundle: owner and origin references, quality.

    Serialization carries bounded opaque references and status metadata
    only - never raw identities, secrets, or physical source details.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_reference: str = Field(min_length=1, max_length=_MAX_OWNER_REFERENCE_CHARS)
    created_by_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    created_at: datetime = Field(default_factory=_utc_now)
    quality: BundleQualityStatus = BundleQualityStatus.DRAFT

    @field_validator("owner_reference")
    @classmethod
    def _safe_owner(cls, value: str) -> str:
        return validate_safe_description(value)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "owner_reference": self.owner_reference,
            "created_by_fingerprint": self.created_by_fingerprint,
            "created_at": self.created_at.isoformat(),
            "quality": self.quality.value,
        }


class BundleCompatibility(BaseModel):
    """Compatibility metadata a catalog checks before activation.

    ``compatible_catalog_fingerprints`` names the catalogs this model may
    bind to; an empty set means the bundle is compatible with any catalog
    the runtime supports.  ``notes`` is bounded safe text.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    compatible_catalog_fingerprints: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_SOURCES
    )
    notes: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)

    @field_validator("compatible_catalog_fingerprints")
    @classmethod
    def _valid_fingerprints(cls, value: frozenset[str]) -> frozenset[str]:
        for fingerprint in value:
            if re.fullmatch(_FINGERPRINT_PATTERN, fingerprint) is None:
                raise ValueError(
                    "compatible catalog references must be sha256 fingerprints"
                )
        return value

    @field_validator("notes")
    @classmethod
    def _safe_notes(cls, value: str) -> str:
        return validate_safe_description(value)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "compatible_catalog_fingerprints": sorted(
                self.compatible_catalog_fingerprints
            ),
            "notes": self.notes,
        }


class SemanticModelBundle(BaseModel):
    """An immutable, versioned semantic model artifact.

    The bundle wraps one validated ``SemanticDescriptor`` (the existing
    entity/field/relationship primitives) and adds measures, grains,
    source references, dependencies, trust markers, compatibility, and
    safe provenance.  The fingerprint is canonical: equivalent contents
    with different mapping insertion orders produce the same identity,
    and once constructed a bundle can never be mutated - a new version
    is a new bundle with a new fingerprint.

    Structural cross-references (measure fields, grain attributes, trust
    fact references, aggregation validity, dependency availability) are
    enforced by :func:`nl2data_core.bundles.validation.validate_bundle`
    so the catalog rejects invalid bundles with structured issues before
    publication or activation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = BUNDLE_SCHEMA_VERSION
    bundle_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    model_version: str = Field(pattern=_IDENTIFIER_PATTERN)
    descriptor: SemanticDescriptor
    measures: tuple[SemanticMeasure, ...] = Field(
        default_factory=tuple, max_length=_MAX_MEASURES
    )
    grains: tuple[SemanticGrain, ...] = Field(
        default_factory=tuple, max_length=_MAX_GRAINS
    )
    sources: tuple[SemanticSourceReference, ...] = Field(
        default_factory=tuple, max_length=_MAX_SOURCES
    )
    dependencies: tuple[BundleDependency, ...] = Field(
        default_factory=tuple, max_length=_MAX_DEPENDENCIES
    )
    trust_markers: tuple[SemanticTrustMarker, ...] = Field(
        default_factory=tuple, max_length=_MAX_TRUST_MARKERS
    )
    compatibility: BundleCompatibility = Field(default_factory=BundleCompatibility)
    provenance: BundleProvenance
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @field_validator("measures")
    @classmethod
    def _unique_measures(
        cls, value: tuple[SemanticMeasure, ...]
    ) -> tuple[SemanticMeasure, ...]:
        ids = [measure.measure_id for measure in value]
        if len(ids) != len(set(ids)):
            raise ValueError("bundle measure ids must be unique")
        return value

    @field_validator("grains")
    @classmethod
    def _unique_grains(cls, value: tuple[SemanticGrain, ...]) -> tuple[SemanticGrain, ...]:
        ids = [grain.grain_id for grain in value]
        if len(ids) != len(set(ids)):
            raise ValueError("bundle grain ids must be unique")
        return value

    @field_validator("sources")
    @classmethod
    def _unique_sources(
        cls, value: tuple[SemanticSourceReference, ...]
    ) -> tuple[SemanticSourceReference, ...]:
        ids = [source.reference_id for source in value]
        if len(ids) != len(set(ids)):
            raise ValueError("bundle source reference ids must be unique")
        return value

    @field_validator("dependencies")
    @classmethod
    def _unique_dependencies(
        cls, value: tuple[BundleDependency, ...]
    ) -> tuple[BundleDependency, ...]:
        ids = [dependency.dependency_id for dependency in value]
        if len(ids) != len(set(ids)):
            raise ValueError("bundle dependency ids must be unique")
        return value

    @field_validator("trust_markers")
    @classmethod
    def _unique_trust_markers(
        cls, value: tuple[SemanticTrustMarker, ...]
    ) -> tuple[SemanticTrustMarker, ...]:
        ids = [marker.marker_id for marker in value]
        if len(ids) != len(set(ids)):
            raise ValueError("bundle trust marker ids must be unique")
        return value

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> SemanticModelBundle:
        fingerprint = sha256_fingerprint(self.canonical_payload())
        object.__setattr__(self, "fingerprint", fingerprint)
        return self

    def canonical_payload(self) -> dict[str, Any]:
        """The canonical payload the fingerprint is derived from."""
        return {
            "schema_version": self.schema_version,
            "bundle_id": self.bundle_id,
            "model_version": self.model_version,
            "descriptor": self.descriptor.canonical_payload(),
            "measures": [measure.canonical_payload() for measure in self.measures],
            "grains": [grain.canonical_payload() for grain in self.grains],
            "sources": [source.canonical_payload() for source in self.sources],
            "dependencies": [
                dependency.canonical_payload() for dependency in self.dependencies
            ],
            "trust_markers": [
                marker.canonical_payload() for marker in self.trust_markers
            ],
            "compatibility": self.compatibility.canonical_payload(),
            "provenance": self.provenance.canonical_payload(),
        }

    def serialize_canonical(self) -> str:
        """Canonical JSON with explicit schema version and sorted keys."""
        return canonical_json(self.canonical_payload())

    def safe_payload(self) -> dict[str, Any]:
        """Serialize with safe references and descriptions only.

        The bundle is safe by construction - canonical payload carries no
        credentials, physical bindings, or authorization claims - and the
        fingerprint itself is included for evidence.
        """
        payload = self.canonical_payload()
        payload["fingerprint"] = self.fingerprint
        return payload

    def entity_ids(self) -> frozenset[str]:
        """Every entity id declared in the wrapped descriptor."""
        return frozenset(entity.entity_id for entity in self.descriptor.entities)

    def field_ids(self) -> frozenset[str]:
        """Every field id declared anywhere in the wrapped descriptor."""
        return self.descriptor.all_field_ids()

    def relationship_ids(self) -> frozenset[str]:
        """Every relationship id declared in the wrapped descriptor."""
        return self.descriptor.all_relationship_ids()
