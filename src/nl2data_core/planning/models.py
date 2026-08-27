"""Backend-neutral physical binding and relationship graph models for compiler context.

The Literal kinds shared by the canonical Semantic Query IR live here so
the IR package never imports adapter code.  ``PhysicalBinding`` is
explicit compiler context: it never enters IR serialization.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nl2data_core.canonical import sha256_fingerprint

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"

AggregationKind = Literal["none", "count", "sum", "avg", "min", "max"]
FilterOperator = Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains"]
OrderDirection = Literal["asc", "desc"]

#: Public scalar set; anything else is a driver-native value and is rejected.
SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool, type(None))


class ColumnBinding(BaseModel):
    """Physical column binding for one semantic field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    physical_name: str = Field(pattern=_IDENTIFIER_PATTERN)
    entity_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "physical_name": self.physical_name,
            "entity_id": self.entity_id,
        }


class EntityBinding(BaseModel):
    """Mapping from a semantic entity to its physical object name."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    physical_name: str = Field(pattern=_IDENTIFIER_PATTERN)

    def canonical_payload(self) -> dict[str, Any]:
        return {"entity_id": self.entity_id, "physical_name": self.physical_name}


class PhysicalBinding(BaseModel):
    """Minimal physical binding used to compile IR cases.

    Contains physical names only - never SQL AST nodes or driver objects.
    The root object is the primary object for the IR; additional entity
    bindings are used for deterministic multi-entity join compilation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    object_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    dialect: str = Field(min_length=1, max_length=32)
    column_bindings: tuple[ColumnBinding, ...] = Field(default_factory=tuple)
    entity_bindings: tuple[EntityBinding, ...] = Field(default_factory=tuple)

    def physical_name(self, field_id: str) -> str | None:
        for binding in self.column_bindings:
            if binding.field_id == field_id:
                return binding.physical_name
        return None

    def entity_for(self, field_id: str) -> str | None:
        for binding in self.column_bindings:
            if binding.field_id == field_id:
                return binding.entity_id
        return None

    def physical_object(self, entity_id: str | None) -> str | None:
        if entity_id is None:
            return self.object_id
        for binding in self.entity_bindings:
            if binding.entity_id == entity_id:
                return binding.physical_name
        return None

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "dialect": self.dialect,
            "column_bindings": [binding.model_dump() for binding in self.column_bindings],
            "entity_bindings": [binding.model_dump() for binding in self.entity_bindings],
        }


class RelationshipEdge(BaseModel):
    """One authorized relationship edge between two semantic entities."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    edge_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    relationship_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    left_entity_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    right_entity_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    left_field_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    right_field_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    cardinality: Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"] = "one_to_many"

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "relationship_id": self.relationship_id,
            "left_entity_id": self.left_entity_id,
            "right_entity_id": self.right_entity_id,
            "left_field_id": self.left_field_id,
            "right_field_id": self.right_field_id,
            "cardinality": self.cardinality,
        }


class RelationshipGraph(BaseModel):
    """Governed directed relationship graph over a single source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    graph_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    edges: tuple[RelationshipEdge, ...] = Field(default_factory=tuple)
    fingerprint: str = Field(default="", pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("edges")
    @classmethod
    def _unique_edge_ids(cls, value: tuple[RelationshipEdge, ...]) -> tuple[RelationshipEdge, ...]:
        ids = [edge.edge_id for edge in value]
        if len(ids) != len(set(ids)):
            raise ValueError("edge ids must be unique")
        return value

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> RelationshipGraph:
        fingerprint = sha256_fingerprint(self.canonical_payload())
        object.__setattr__(self, "fingerprint", fingerprint)
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "source_id": self.source_id,
            "edges": [
                edge.canonical_payload()
                for edge in sorted(self.edges, key=lambda e: e.edge_id)
            ],
        }
