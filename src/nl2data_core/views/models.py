"""Immutable bounded Semantic View contract models.

A Semantic View binds a bounded semantic descriptor (entities, fields,
relationships, operations, result shapes) to trusted governance context.
The models here are host-supplied inputs to the resolver: definitions and
descriptors carry only semantic references and safe descriptions - never
credentials, physical bindings, hidden policy rules, or native objects.

Every collection is bounded, every model is frozen, and fingerprints are
computed over the canonical payload so equivalent inputs with different
mapping insertion orders produce identical identities.  Mappings are
deeply immutable so a resolved view can never be mutated after binding.
"""

from __future__ import annotations

import re
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.planning.models import AggregationKind

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

#: Bounded collection and text limits for every view/descriptor model.
_MAX_ENTITIES = 512
_MAX_FIELDS = 4_096
_MAX_RELATIONSHIPS = 1_024
_MAX_OPERATIONS = 32
_MAX_PURPOSES = 64
_MAX_ALIASES = 4_096
_MAX_CAPABILITIES = 64
_MAX_FEATURE_FLAGS = 64
_MAX_PRINCIPAL_BINDINGS = 64
_MAX_DESCRIPTION_CHARS = 1_024
_MAX_LABEL_CHARS = 256
_UNSAFE_DESCRIPTION_MARKERS = (
    "password=",
    "secret=",
    "token=",
    "api_key=",
    "postgres://",
    "postgresql://",
    "mongodb://",
    "redis://",
    "jdbc:",
)
_SQL_TEXT = re.compile(
    r"\b(select|insert|update|delete|drop|create|alter|merge)\b[\s\S]{0,200}\b(from|into|set|table|values)\b",
    re.IGNORECASE,
)


class _FrozenDict(dict[str, Any]):
    """A deeply immutable mapping; mutation raises ``TypeError``."""

    def _raise_immutable(self) -> None:
        raise TypeError("view mappings are immutable")

    def __setitem__(self, key: str, value: Any) -> None:
        self._raise_immutable()

    def __delitem__(self, key: str) -> None:
        self._raise_immutable()

    def __ior__(self, value: Any) -> _FrozenDict:  # type: ignore[override,misc]
        self._raise_immutable()
        raise AssertionError("unreachable")

    def clear(self) -> None:
        self._raise_immutable()

    def pop(self, key: str, default: Any = None) -> Any:
        self._raise_immutable()
        raise AssertionError("unreachable")

    def popitem(self) -> tuple[Any, Any]:
        self._raise_immutable()
        raise AssertionError("unreachable")

    def setdefault(self, key: str, default: Any = None) -> Any:
        self._raise_immutable()
        raise AssertionError("unreachable")

    def update(self, *args: Any, **kwargs: Any) -> None:
        self._raise_immutable()


def _freeze_mapping(value: dict[str, str]) -> dict[str, str]:
    return cast(dict[str, str], _FrozenDict(value))


def validate_safe_description(value: str) -> str:
    """Reject credential/connection/executable material in semantic text.

    Shared by the view models and the Semantic Model Bundle metadata so
    safe-content rules are never duplicated across artifact boundaries.
    """
    lowered = value.lower()
    if any(marker in lowered for marker in _UNSAFE_DESCRIPTION_MARKERS):
        raise ValueError("semantic descriptions cannot contain credential or connection material")
    if _SQL_TEXT.search(value):
        raise ValueError("semantic descriptions cannot contain executable SQL material")
    return value


class SemanticFieldDescriptor(BaseModel):
    """One bounded semantic field with safe catalog descriptions only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    label: str = Field(min_length=1, max_length=_MAX_LABEL_CHARS)
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)
    data_type: str = Field(pattern=_IDENTIFIER_PATTERN)
    allowed_aggregations: frozenset[AggregationKind] = Field(default_factory=frozenset)

    @field_validator("description")
    @classmethod
    def _safe_description(cls, value: str) -> str:
        return validate_safe_description(value)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "label": self.label,
            "description": self.description,
            "data_type": self.data_type,
            "allowed_aggregations": sorted(self.allowed_aggregations),
        }


class SemanticRelationshipDescriptor(BaseModel):
    """One bounded semantic relationship between two entities."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    relationship_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    source_entity_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    target_entity_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    label: str = Field(min_length=1, max_length=_MAX_LABEL_CHARS)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "relationship_id": self.relationship_id,
            "source_entity_id": self.source_entity_id,
            "target_entity_id": self.target_entity_id,
            "label": self.label,
        }


class SemanticEntityDescriptor(BaseModel):
    """One bounded semantic entity with its fields and relationships."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    label: str = Field(min_length=1, max_length=_MAX_LABEL_CHARS)
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)
    fields: tuple[SemanticFieldDescriptor, ...] = Field(
        default_factory=tuple, max_length=_MAX_FIELDS
    )
    relationships: tuple[SemanticRelationshipDescriptor, ...] = Field(
        default_factory=tuple, max_length=_MAX_RELATIONSHIPS
    )

    @field_validator("description")
    @classmethod
    def _safe_description(cls, value: str) -> str:
        return validate_safe_description(value)

    @field_validator("fields")
    @classmethod
    def _unique_fields(
        cls, value: tuple[SemanticFieldDescriptor, ...]
    ) -> tuple[SemanticFieldDescriptor, ...]:
        ids = [field.field_id for field in value]
        if len(ids) != len(set(ids)):
            raise ValueError("entity field ids must be unique")
        return value

    @field_validator("relationships")
    @classmethod
    def _unique_relationships(
        cls, value: tuple[SemanticRelationshipDescriptor, ...]
    ) -> tuple[SemanticRelationshipDescriptor, ...]:
        ids = [relationship.relationship_id for relationship in value]
        if len(ids) != len(set(ids)):
            raise ValueError("entity relationship ids must be unique")
        return value

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "label": self.label,
            "description": self.description,
            "fields": [field.canonical_payload() for field in self.fields],
            "relationships": [
                relationship.canonical_payload() for relationship in self.relationships
            ],
        }


class SemanticDescriptor(BaseModel):
    """A bounded semantic descriptor consumed by view definitions.

    The fingerprint covers the descriptor identity, version, source, and
    catalog reference plus every entity, field, and relationship payload -
    never physical bindings or hidden metadata.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    descriptor_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    version: int = Field(ge=1, le=1_000_000)
    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    catalog_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    entities: tuple[SemanticEntityDescriptor, ...] = Field(
        default_factory=tuple, max_length=_MAX_ENTITIES
    )
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @field_validator("entities")
    @classmethod
    def _unique_entities(
        cls, value: tuple[SemanticEntityDescriptor, ...]
    ) -> tuple[SemanticEntityDescriptor, ...]:
        ids = [entity.entity_id for entity in value]
        if len(ids) != len(set(ids)):
            raise ValueError("descriptor entity ids must be unique")
        field_ids = [
            field.field_id for entity in value for field in entity.fields
        ]
        if len(field_ids) != len(set(field_ids)):
            raise ValueError("descriptor field ids must be unique across entities")
        relationship_ids = [
            relationship.relationship_id
            for entity in value
            for relationship in entity.relationships
        ]
        if len(relationship_ids) != len(set(relationship_ids)):
            raise ValueError("descriptor relationship ids must be unique across entities")
        entity_id_set = set(ids)
        for entity in value:
            for relationship in entity.relationships:
                if (
                    relationship.source_entity_id not in entity_id_set
                    or relationship.target_entity_id not in entity_id_set
                ):
                    raise ValueError(
                        "relationship source and target entity ids must exist in the descriptor"
                    )
        return value

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> SemanticDescriptor:
        fingerprint = sha256_fingerprint(self.canonical_payload())
        object.__setattr__(self, "fingerprint", fingerprint)
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "descriptor_id": self.descriptor_id,
            "version": self.version,
            "source_id": self.source_id,
            "catalog_fingerprint": self.catalog_fingerprint,
            "entities": [entity.canonical_payload() for entity in self.entities],
        }

    def entity(self, entity_id: str) -> SemanticEntityDescriptor | None:
        """The entity with the given id, or ``None`` when absent."""
        for entity in self.entities:
            if entity.entity_id == entity_id:
                return entity
        return None

    def field(self, field_id: str) -> SemanticFieldDescriptor | None:
        """The first field with the given id, or ``None`` when absent."""
        for entity in self.entities:
            for field in entity.fields:
                if field.field_id == field_id:
                    return field
        return None

    def all_field_ids(self) -> frozenset[str]:
        """Every field id declared anywhere in the descriptor."""
        return frozenset(
            field.field_id for entity in self.entities for field in entity.fields
        )

    def all_relationship_ids(self) -> frozenset[str]:
        """Every relationship id declared anywhere in the descriptor."""
        return frozenset(
            relationship.relationship_id
            for entity in self.entities
            for relationship in entity.relationships
        )


class ViewProvenance(BaseModel):
    """Safe provenance of a view definition or resolved projection.

    When bundle-backed catalog resolution is configured, the provenance
    carries the active bundle identity/version/fingerprint (all-or-none)
    so every resolved projection and its evidence can be revalidated
    against the bundle that produced it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    descriptor_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    policy_decision_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    resolver_version: int = Field(ge=1, le=1_000_000)
    bundle_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    bundle_version: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    bundle_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _bundle_identity_consistency(self) -> ViewProvenance:
        bundle_fields = [self.bundle_id, self.bundle_version, self.bundle_fingerprint]
        if any(value is not None for value in bundle_fields) and not all(
            value is not None for value in bundle_fields
        ):
            raise ValueError(
                "bundle provenance requires bundle_id, bundle_version, and "
                "bundle_fingerprint together"
            )
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "descriptor_fingerprint": self.descriptor_fingerprint,
            "policy_decision_fingerprint": self.policy_decision_fingerprint,
            "resolver_version": self.resolver_version,
            "bundle_id": self.bundle_id,
            "bundle_version": self.bundle_version,
            "bundle_fingerprint": self.bundle_fingerprint,
        }


class ViewMemberRestrictions(BaseModel):
    """Constraints a view applies over the descriptor.

    Restrictions are constraints, not authority: they can only narrow the
    descriptor surface, never grant members the descriptor or trusted
    policy context does not already allow.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    include_entities: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_ENTITIES
    )
    exclude_entities: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_ENTITIES
    )
    include_fields: frozenset[str] = Field(default_factory=frozenset, max_length=_MAX_FIELDS)
    exclude_fields: frozenset[str] = Field(default_factory=frozenset, max_length=_MAX_FIELDS)
    field_aliases: dict[str, str] = Field(default_factory=dict, max_length=_MAX_ALIASES)
    allowed_operations: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_OPERATIONS
    )
    field_aggregation_restrictions: dict[str, frozenset[AggregationKind]] = Field(
        default_factory=dict, max_length=_MAX_FIELDS
    )
    allowed_relationships: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_RELATIONSHIPS
    )
    result_shape_constraints: tuple[str, ...] = Field(default_factory=tuple, max_length=16)

    @field_validator("field_aliases", mode="after")
    @classmethod
    def _freeze_aliases(cls, value: dict[str, str]) -> dict[str, str]:
        for field_id, alias in value.items():
            if re.fullmatch(_IDENTIFIER_PATTERN, field_id) is None or re.fullmatch(
                _IDENTIFIER_PATTERN, alias
            ) is None:
                raise ValueError("field aliases must map bounded identifiers to identifiers")
        return _freeze_mapping(value)

    @field_validator("field_aggregation_restrictions", mode="after")
    @classmethod
    def _freeze_aggregation_restrictions(
        cls, value: dict[str, frozenset[AggregationKind]]
    ) -> dict[str, frozenset[AggregationKind]]:
        for field_id in value:
            if re.fullmatch(_IDENTIFIER_PATTERN, field_id) is None:
                raise ValueError(
                    "aggregation restriction keys must be bounded identifiers"
                )
        return cast(dict[str, frozenset[AggregationKind]], _FrozenDict(value))

    @field_validator("allowed_operations")
    @classmethod
    def _valid_operations(cls, value: frozenset[str]) -> frozenset[str]:
        for operation in value:
            if re.fullmatch(_IDENTIFIER_PATTERN, operation) is None:
                raise ValueError("allowed operations must be bounded identifiers")
        return value

    @field_validator("allowed_relationships")
    @classmethod
    def _valid_relationships(cls, value: frozenset[str]) -> frozenset[str]:
        for relationship_id in value:
            if re.fullmatch(_IDENTIFIER_PATTERN, relationship_id) is None:
                raise ValueError("allowed relationship ids must be bounded identifiers")
        return value

    @field_validator("result_shape_constraints")
    @classmethod
    def _valid_result_shapes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for kind in value:
            if kind not in {"rows", "grouped_rows", "scalar"}:
                raise ValueError("result shape constraints must be known IR shapes")
        if len(value) != len(set(value)):
            raise ValueError("result shape constraints must be unique")
        return value

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "include_entities": sorted(self.include_entities),
            "exclude_entities": sorted(self.exclude_entities),
            "include_fields": sorted(self.include_fields),
            "exclude_fields": sorted(self.exclude_fields),
            "field_aliases": dict(sorted(self.field_aliases.items())),
            "allowed_operations": sorted(self.allowed_operations),
            "field_aggregation_restrictions": {
                field_id: sorted(aggregations)
                for field_id, aggregations in sorted(
                    self.field_aggregation_restrictions.items()
                )
            },
            "allowed_relationships": sorted(self.allowed_relationships),
            "result_shape_constraints": list(self.result_shape_constraints),
        }


class SemanticViewDefinition(BaseModel):
    """An immutable versioned Semantic View definition.

    The fingerprint covers the view identity, version, descriptor binding,
    purposes, restrictions, bound policy/tenant/principal references, and
    required capabilities/feature flags - the stable identity every
    resolved projection and workflow checkpoint reference.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    view_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    version: int = Field(ge=1, le=1_000_000)
    descriptor_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)
    allowed_purposes: frozenset[str] = Field(default_factory=frozenset, max_length=_MAX_PURPOSES)
    restrictions: ViewMemberRestrictions = Field(default_factory=ViewMemberRestrictions)
    bound_policy_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    bound_tenant_scope_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    bound_principal_authorization_fingerprints: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_PRINCIPAL_BINDINGS
    )
    required_capabilities: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_CAPABILITIES
    )
    required_feature_flags: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_FEATURE_FLAGS
    )
    model_version: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    provenance: ViewProvenance
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @field_validator("description")
    @classmethod
    def _safe_description(cls, value: str) -> str:
        return validate_safe_description(value)

    @field_validator("allowed_purposes")
    @classmethod
    def _valid_purposes(cls, value: frozenset[str]) -> frozenset[str]:
        for purpose in value:
            if re.fullmatch(_IDENTIFIER_PATTERN, purpose) is None:
                raise ValueError("allowed purposes must be bounded identifiers")
        return value

    @field_validator("bound_principal_authorization_fingerprints")
    @classmethod
    def _valid_principal_bindings(cls, value: frozenset[str]) -> frozenset[str]:
        for fingerprint in value:
            if re.fullmatch(_FINGERPRINT_PATTERN, fingerprint) is None:
                raise ValueError(
                    "principal authorization bindings must be sha256 fingerprints"
                )
        return value

    @field_validator("required_capabilities")
    @classmethod
    def _valid_capabilities(cls, value: frozenset[str]) -> frozenset[str]:
        for capability in value:
            if re.fullmatch(_IDENTIFIER_PATTERN, capability) is None:
                raise ValueError("required capabilities must be bounded identifiers")
        return value

    @field_validator("required_feature_flags")
    @classmethod
    def _valid_feature_flags(cls, value: frozenset[str]) -> frozenset[str]:
        for flag in value:
            if re.fullmatch(_IDENTIFIER_PATTERN, flag) is None:
                raise ValueError("required feature flags must be bounded identifiers")
        return value

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> SemanticViewDefinition:
        fingerprint = sha256_fingerprint(self.canonical_payload())
        object.__setattr__(self, "fingerprint", fingerprint)
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "view_id": self.view_id,
            "version": self.version,
            "descriptor_id": self.descriptor_id,
            "description": self.description,
            "allowed_purposes": sorted(self.allowed_purposes),
            "restrictions": self.restrictions.canonical_payload(),
            "bound_policy_fingerprint": self.bound_policy_fingerprint,
            "bound_tenant_scope_fingerprint": self.bound_tenant_scope_fingerprint,
            "bound_principal_authorization_fingerprints": sorted(
                self.bound_principal_authorization_fingerprints
            ),
            "required_capabilities": sorted(self.required_capabilities),
            "required_feature_flags": sorted(self.required_feature_flags),
            "model_version": self.model_version,
            "provenance": self.provenance.canonical_payload(),
        }
