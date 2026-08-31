"""Immutable resolved Semantic View projections.

A projection is the authorized semantic surface handed to planning and
provider-context assembly: only permitted entities, fields, operations,
relationships, and safe descriptions, plus versioned provenance.  The
fingerprint covers every security dimension - view identity/version,
model/catalog fingerprint, active bundle identity/version/fingerprint when
configured, tenant scope, principal authorization, purpose, policy,
adapter capability fingerprint, and feature flags - so a change in any
trusted input invalidates every previously recorded projection, IR
reference, and workflow evidence reference.

Raw identity claims, credentials, physical bindings, and hidden policy
rules never appear in the serialized projection.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.planning.models import AggregationKind

from .models import ViewProvenance

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

_MAX_ENTITIES = 512
_MAX_FIELDS = 4_096
_MAX_RELATIONSHIPS = 1_024
_MAX_OPERATIONS = 32
_MAX_DESCRIPTION_CHARS = 1_024
_MAX_LABEL_CHARS = 256


class ResolvedViewField(BaseModel):
    """One permitted semantic field of a resolved projection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    alias: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    label: str = Field(min_length=1, max_length=_MAX_LABEL_CHARS)
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)
    data_type: str = Field(pattern=_IDENTIFIER_PATTERN)
    allowed_aggregations: frozenset[AggregationKind] = Field(default_factory=frozenset)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "alias": self.alias,
            "label": self.label,
            "description": self.description,
            "data_type": self.data_type,
            "allowed_aggregations": sorted(self.allowed_aggregations),
        }


class ResolvedCalculatedField(BaseModel):
    """Bounded calculated-field identity authorized for prompt context (D10).

    Carries name, label, description, and output type only - never the
    expression tree, the dependency list, or the zero-division policy.  The
    model references a calculated field by name only (N4); expansion
    material never crosses the provider boundary.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=_IDENTIFIER_PATTERN)
    label: str = Field(min_length=1, max_length=_MAX_LABEL_CHARS)
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)
    output_type: Literal["int", "float"]
    content_hash: str = Field(pattern=_FINGERPRINT_PATTERN)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "output_type": self.output_type,
            "content_hash": self.content_hash,
        }


class ResolvedViewEntity(BaseModel):
    """One permitted semantic entity of a resolved projection.

    ``relationships`` carries only permitted relationship ids - never
    relationship internals or hidden metadata.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    label: str = Field(min_length=1, max_length=_MAX_LABEL_CHARS)
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)
    fields: tuple[ResolvedViewField, ...] = Field(
        default_factory=tuple, max_length=_MAX_FIELDS
    )
    relationships: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_RELATIONSHIPS
    )

    @model_validator(mode="after")
    def _unique_fields(self) -> ResolvedViewEntity:
        ids = [field.field_id for field in self.fields]
        if len(ids) != len(set(ids)):
            raise ValueError("projection entity field ids must be unique")
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "label": self.label,
            "description": self.description,
            "fields": [field.canonical_payload() for field in self.fields],
            "relationships": sorted(self.relationships),
        }


class ResolvedViewProjection(BaseModel):
    """Immutable authorized projection of one resolved Semantic View.

    The fingerprint is derived from the canonical payload and covers view
    identity/version, model/catalog, tenant scope, principal authorization,
    purpose, policy, adapter capabilities, and feature flags - every
    security dimension the view binds to.  It never includes the raw
    fingerprint inputs themselves, so the projection is stable across
    equivalent resolutions with different mapping insertion orders.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    view_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    view_version: int = Field(ge=1, le=1_000_000)
    descriptor_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)
    root_entity_ids: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_ENTITIES
    )
    field_ids: frozenset[str] = Field(default_factory=frozenset, max_length=_MAX_FIELDS)
    calculated_field_ids: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_FIELDS
    )
    #: Bounded calculated-field identity for prompt context (D10); unset on
    #: projections whose entities declare no calculated fields.
    calculated_fields: tuple[ResolvedCalculatedField, ...] | None = Field(
        default=None, max_length=_MAX_FIELDS
    )
    entities: tuple[ResolvedViewEntity, ...] = Field(
        default_factory=tuple, max_length=_MAX_ENTITIES
    )
    allowed_operations: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_OPERATIONS
    )
    allowed_relationships: frozenset[str] = Field(
        default_factory=frozenset, max_length=_MAX_RELATIONSHIPS
    )
    result_shape_constraints: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    catalog_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    bundle_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    bundle_version: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    bundle_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    policy_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    tenant_scope_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    principal_authorization_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    purpose: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    model_version: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    adapter_capability_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    feature_flag_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    provenance: ViewProvenance
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> ResolvedViewProjection:
        entity_ids = [entity.entity_id for entity in self.entities]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("projection entity ids must be unique")
        projected_field_ids = [
            field.field_id for entity in self.entities for field in entity.fields
        ]
        if len(projected_field_ids) != len(set(projected_field_ids)):
            raise ValueError("projection field ids must be unique across entities")
        if not frozenset(projected_field_ids).issubset(self.field_ids):
            raise ValueError("projection field_ids must include every projected entity field")
        if not frozenset(entity_ids).issubset(self.root_entity_ids):
            raise ValueError("projection root_entity_ids must include every projected entity")
        if self.calculated_field_ids & self.field_ids:
            raise ValueError(
                "projection calculated_field_ids must not collide with projected field ids"
            )
        if self.calculated_fields:
            names = [item.name for item in self.calculated_fields]
            if len(names) != len(set(names)):
                raise ValueError("projection calculated field names must be unique")
            if not names == sorted(names):
                raise ValueError("projection calculated fields must be sorted by name")
            if set(names) != self.calculated_field_ids:
                raise ValueError(
                    "projection calculated_fields must match calculated_field_ids"
                )
        bundle_fields = [self.bundle_id, self.bundle_version, self.bundle_fingerprint]
        if any(value is not None for value in bundle_fields) and not all(
            value is not None for value in bundle_fields
        ):
            raise ValueError(
                "projection bundle binding requires bundle_id, bundle_version, "
                "and bundle_fingerprint together"
            )
        fingerprint = sha256_fingerprint(self.canonical_payload())
        object.__setattr__(self, "fingerprint", fingerprint)
        return self

    def canonical_payload(self) -> dict[str, Any]:
        """The canonical payload the fingerprint is derived from."""
        return {
            "view_id": self.view_id,
            "view_version": self.view_version,
            "descriptor_id": self.descriptor_id,
            "source_id": self.source_id,
            "description": self.description,
            "root_entity_ids": sorted(self.root_entity_ids),
            "field_ids": sorted(self.field_ids),
            # N6: unset optional members are omitted entirely so introducing
            # calculated fields cannot change the fingerprint of any
            # projection that does not use them.
            **(
                {"calculated_field_ids": sorted(self.calculated_field_ids)}
                if self.calculated_field_ids
                else {}
            ),
            # N6: unset optional members are omitted entirely so introducing
            # calculated-field prompt identity cannot change the fingerprint
            # of any projection that does not use it.
            **(
                {
                    "calculated_fields": [
                        item.canonical_payload() for item in self.calculated_fields
                    ]
                }
                if self.calculated_fields
                else {}
            ),
            "entities": [entity.canonical_payload() for entity in self.entities],
            "allowed_operations": sorted(self.allowed_operations),
            "allowed_relationships": sorted(self.allowed_relationships),
            "result_shape_constraints": list(self.result_shape_constraints),
            "catalog_fingerprint": self.catalog_fingerprint,
            "bundle_id": self.bundle_id,
            "bundle_version": self.bundle_version,
            "bundle_fingerprint": self.bundle_fingerprint,
            "policy_fingerprint": self.policy_fingerprint,
            "tenant_scope_fingerprint": self.tenant_scope_fingerprint,
            "principal_authorization_fingerprint": self.principal_authorization_fingerprint,
            "purpose": self.purpose,
            "model_version": self.model_version,
            "adapter_capability_fingerprint": self.adapter_capability_fingerprint,
            "feature_flag_fingerprint": self.feature_flag_fingerprint,
            "provenance": self.provenance.canonical_payload(),
        }

    def safe_payload(self) -> dict[str, Any]:
        """Serialize with safe references and descriptions only.

        Includes the fingerprint itself; excludes nothing that is unsafe -
        raw identities, credentials, physical bindings, and hidden policy
        rules are structurally absent from the canonical payload.
        """
        payload = self.canonical_payload()
        payload["fingerprint"] = self.fingerprint
        return payload

    def contains_field(self, field_id: str) -> bool:
        """Whether the projection permits the given semantic field."""
        return field_id in self.field_ids

    def contains_calculated_field(self, calculated_name: str) -> bool:
        """Whether the projection permits the given calculated-field name."""
        return calculated_name in self.calculated_field_ids

    def contains_relationship(self, relationship_id: str) -> bool:
        """Whether the projection permits the given relationship."""
        return relationship_id in self.allowed_relationships

    def contains_operation(self, operation: str) -> bool:
        """Whether the projection permits the given semantic operation."""
        return operation in self.allowed_operations
