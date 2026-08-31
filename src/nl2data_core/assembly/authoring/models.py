"""Bounded semantic-only contracts for human-authored assembly documents."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from nl2data_core.bundles.models import BundleCompatibility
from nl2data_core.planning.models import AggregationKind
from nl2data_core.views.models import (
    CalculatedOutputType,
    ExprNode,
    ValueSemantics,
    validate_safe_description,
)

AUTHORING_API_VERSION: Literal["nl2data.io/semantic-assembly-authoring/v1alpha1"] = (
    "nl2data.io/semantic-assembly-authoring/v1alpha1"
)
AUTHORING_KIND: Literal["SemanticAssembly"] = "SemanticAssembly"

MAX_AUTHORING_BYTES = 1_048_576
MAX_AUTHORING_ENTITIES = 1_024
MAX_AUTHORING_FIELDS = 4_096
MAX_AUTHORING_RELATIONSHIPS = 4_096
MAX_AUTHORING_CALCULATED_FIELDS = 1_024
MAX_AUTHORING_MEASURES = 4_096
MAX_AUTHORING_GRAINS = 256
MAX_AUTHORING_SOURCE_REFERENCES = 64
MAX_AUTHORING_DEPLOYMENT_BINDINGS = 64

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_MAX_DESCRIPTION_CHARS = 1_024
_MAX_LABEL_CHARS = 256
_MAX_JOIN_FIELDS = 128

Identifier = Annotated[str, StringConstraints(pattern=_IDENTIFIER_PATTERN)]
Fingerprint = Annotated[str, StringConstraints(pattern=_FINGERPRINT_PATTERN)]


def _camel_alias(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class _AuthoringModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel_alias,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


class AuthoringMetadata(_AuthoringModel):
    bundle_id: Identifier
    model_version: Identifier
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)

    @field_validator("description")
    @classmethod
    def _safe_description(cls, value: str) -> str:
        return validate_safe_description(value)


class AuthoringSource(_AuthoringModel):
    source_id: Identifier
    catalog_fingerprint: Fingerprint | None = None


class AuthoringField(_AuthoringModel):
    field_id: Identifier
    label: str = Field(min_length=1, max_length=_MAX_LABEL_CHARS)
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)
    data_type: Identifier
    allowed_aggregations: frozenset[AggregationKind] = Field(default_factory=frozenset)
    value_semantics: ValueSemantics | None = None

    @field_validator("description")
    @classmethod
    def _safe_description(cls, value: str) -> str:
        return validate_safe_description(value)


class AuthoringRelationship(_AuthoringModel):
    relationship_id: Identifier
    target_entity_id: Identifier
    source_fields: tuple[Identifier, ...] = Field(min_length=1, max_length=_MAX_JOIN_FIELDS)
    target_fields: tuple[Identifier, ...] = Field(min_length=1, max_length=_MAX_JOIN_FIELDS)
    label: str = Field(min_length=1, max_length=_MAX_LABEL_CHARS)

    @model_validator(mode="after")
    def _matching_unique_join_fields(self) -> AuthoringRelationship:
        if len(self.source_fields) != len(self.target_fields):
            raise ValueError("relationship source and target fields must have matching lengths")
        if len(set(self.source_fields)) != len(self.source_fields):
            raise ValueError("relationship source fields must be unique")
        if len(set(self.target_fields)) != len(self.target_fields):
            raise ValueError("relationship target fields must be unique")
        return self


class AuthoringCalculatedField(_AuthoringModel):
    name: Identifier
    label: str = Field(min_length=1, max_length=_MAX_LABEL_CHARS)
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)
    expression: ExprNode
    output_type: CalculatedOutputType
    requires: tuple[Identifier, ...] = Field(max_length=MAX_AUTHORING_FIELDS)
    zero_division_policy: Literal["null", "error"] = "null"

    @field_validator("description")
    @classmethod
    def _safe_description(cls, value: str) -> str:
        return validate_safe_description(value)

    @model_validator(mode="after")
    def _requires_match_expression(self) -> AuthoringCalculatedField:
        if len(set(self.requires)) != len(self.requires):
            raise ValueError("calculated-field requires entries must be unique")
        if set(self.requires) != self.expression.field_leaves():
            raise ValueError("calculated-field requires must match expression field leaves")
        return self


class AuthoringEntity(_AuthoringModel):
    entity_id: Identifier
    label: str = Field(min_length=1, max_length=_MAX_LABEL_CHARS)
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)
    fields: tuple[AuthoringField, ...] = Field(
        default_factory=tuple,
        max_length=MAX_AUTHORING_FIELDS,
    )
    relationships: tuple[AuthoringRelationship, ...] = Field(
        default_factory=tuple,
        max_length=MAX_AUTHORING_RELATIONSHIPS,
    )
    calculated_fields: tuple[AuthoringCalculatedField, ...] = Field(
        default_factory=tuple,
        max_length=MAX_AUTHORING_CALCULATED_FIELDS,
    )

    @field_validator("description")
    @classmethod
    def _safe_description(cls, value: str) -> str:
        return validate_safe_description(value)


class AuthoringMeasure(_AuthoringModel):
    measure_id: Identifier
    field_id: Identifier
    aggregation: AggregationKind = "none"
    label: str = Field(min_length=1, max_length=_MAX_LABEL_CHARS)
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)

    @field_validator("description")
    @classmethod
    def _safe_description(cls, value: str) -> str:
        return validate_safe_description(value)


class AuthoringGrain(_AuthoringModel):
    grain_id: Identifier
    entity_id: Identifier
    attributes: frozenset[Identifier] = Field(
        default_factory=frozenset,
        max_length=MAX_AUTHORING_FIELDS,
    )
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)

    @field_validator("description")
    @classmethod
    def _safe_description(cls, value: str) -> str:
        return validate_safe_description(value)


class AuthoringSourceReference(_AuthoringModel):
    reference_id: Identifier
    source_id: Identifier
    catalog_fingerprint: Fingerprint | None = None
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)

    @field_validator("description")
    @classmethod
    def _safe_description(cls, value: str) -> str:
        return validate_safe_description(value)


class AuthoringDeploymentBinding(_AuthoringModel):
    binding_id: Identifier
    environment: Identifier
    source_id: Identifier
    connection_reference: str = Field(min_length=1, max_length=256)


class AuthoringSpec(_AuthoringModel):
    source: AuthoringSource
    entities: tuple[AuthoringEntity, ...] = Field(
        min_length=1,
        max_length=MAX_AUTHORING_ENTITIES,
    )
    measures: tuple[AuthoringMeasure, ...] = Field(
        default_factory=tuple,
        max_length=MAX_AUTHORING_MEASURES,
    )
    grains: tuple[AuthoringGrain, ...] = Field(
        default_factory=tuple,
        max_length=MAX_AUTHORING_GRAINS,
    )
    source_references: tuple[AuthoringSourceReference, ...] = Field(
        default_factory=tuple,
        max_length=MAX_AUTHORING_SOURCE_REFERENCES,
    )
    compatibility: BundleCompatibility = Field(default_factory=BundleCompatibility)
    deployment_bindings: tuple[AuthoringDeploymentBinding, ...] = Field(
        default_factory=tuple,
        max_length=MAX_AUTHORING_DEPLOYMENT_BINDINGS,
    )


class SemanticAssemblyAuthoring(_AuthoringModel):
    api_version: Literal["nl2data.io/semantic-assembly-authoring/v1alpha1"] = Field(
        alias="apiVersion"
    )
    kind: Literal["SemanticAssembly"]
    metadata: AuthoringMetadata
    spec: AuthoringSpec

    @model_validator(mode="after")
    def _validate_global_identities_and_sources(self) -> SemanticAssemblyAuthoring:
        entities = self.spec.entities
        self._require_total_at_most(
            "fields",
            sum(len(entity.fields) for entity in entities),
            MAX_AUTHORING_FIELDS,
        )
        self._require_total_at_most(
            "relationships",
            sum(len(entity.relationships) for entity in entities),
            MAX_AUTHORING_RELATIONSHIPS,
        )
        self._require_total_at_most(
            "calculated fields",
            sum(len(entity.calculated_fields) for entity in entities),
            MAX_AUTHORING_CALCULATED_FIELDS,
        )
        self._require_unique("entity", [entity.entity_id for entity in entities])
        self._require_unique(
            "field",
            [field.field_id for entity in entities for field in entity.fields],
        )
        self._require_unique(
            "relationship",
            [
                relationship.relationship_id
                for entity in entities
                for relationship in entity.relationships
            ],
        )
        calculated_names = [
            calculated.name for entity in entities for calculated in entity.calculated_fields
        ]
        self._require_unique("calculated field", calculated_names)
        field_ids = {field.field_id for entity in entities for field in entity.fields}
        collisions = field_ids.intersection(calculated_names)
        if collisions:
            raise ValueError("calculated field names must not collide with field ids")
        self._require_unique("measure", [measure.measure_id for measure in self.spec.measures])
        self._require_unique("grain", [grain.grain_id for grain in self.spec.grains])
        self._require_unique(
            "source reference",
            [reference.reference_id for reference in self.spec.source_references],
        )
        self._require_unique(
            "deployment binding",
            [binding.binding_id for binding in self.spec.deployment_bindings],
        )
        source_id = self.spec.source.source_id
        if any(reference.source_id != source_id for reference in self.spec.source_references):
            raise ValueError("source references must match the document source")
        if any(binding.source_id != source_id for binding in self.spec.deployment_bindings):
            raise ValueError("deployment bindings must match the document source")
        return self

    @staticmethod
    def _require_unique(kind: str, values: list[str]) -> None:
        if len(values) != len(set(values)):
            raise ValueError(f"authoring {kind} identities must be descriptor-global and unique")

    @staticmethod
    def _require_total_at_most(kind: str, count: int, maximum: int) -> None:
        if count > maximum:
            raise ValueError(f"authoring {kind} must contain at most {maximum} items")

    def authoring_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)
