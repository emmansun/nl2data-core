"""Safe snapshot comparison and schema drift detection.

Comparison works on canonical identities only: it reports added, removed,
and changed object/field/type/constraint/relationship references between
two compatible snapshots, never raw values.  A changed snapshot invalidates
compatible bundle/view assumptions unless the host publishes a new
compatible bundle; consumers can check :attr:`SnapshotComparison.equivalent`
before activating dependent artifacts.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    MetadataConstraint,
    MetadataRelationship,
    MetadataSnapshot,
)

#: Bounded number of change references reported by one comparison.
_MAX_CHANGES = 4_096


class ObjectChange(BaseModel):
    """One safe object-level change reference."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    object_id: str
    field_ids: frozenset[str] = Field(default_factory=frozenset, max_length=16_384)

    def canonical_payload(self) -> dict[str, Any]:
        return {"object_id": self.object_id, "field_ids": sorted(self.field_ids)}


class FieldTypeChange(BaseModel):
    """One field whose normalized type changed between snapshots."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    object_id: str
    field_id: str
    before_type: str | None
    after_type: str | None

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "field_id": self.field_id,
            "before_type": self.before_type,
            "after_type": self.after_type,
        }


class SnapshotComparison(BaseModel):
    """Bounded safe changes between two compatible snapshots.

    Every member is a reference - object/field/constraint/relationship
    identifiers and normalized type names - never raw values, credentials,
    or native objects.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    added_objects: tuple[str, ...] = Field(default_factory=tuple, max_length=_MAX_CHANGES)
    removed_objects: tuple[str, ...] = Field(default_factory=tuple, max_length=_MAX_CHANGES)
    changed_objects: tuple[ObjectChange, ...] = Field(
        default_factory=tuple, max_length=_MAX_CHANGES
    )
    added_fields: tuple[ObjectChange, ...] = Field(
        default_factory=tuple, max_length=_MAX_CHANGES
    )
    removed_fields: tuple[ObjectChange, ...] = Field(
        default_factory=tuple, max_length=_MAX_CHANGES
    )
    changed_field_types: tuple[FieldTypeChange, ...] = Field(
        default_factory=tuple, max_length=_MAX_CHANGES
    )
    added_constraints: tuple[str, ...] = Field(default_factory=tuple, max_length=_MAX_CHANGES)
    removed_constraints: tuple[str, ...] = Field(default_factory=tuple, max_length=_MAX_CHANGES)
    changed_constraints: tuple[str, ...] = Field(default_factory=tuple, max_length=_MAX_CHANGES)
    added_relationships: tuple[str, ...] = Field(
        default_factory=tuple, max_length=_MAX_CHANGES
    )
    removed_relationships: tuple[str, ...] = Field(
        default_factory=tuple, max_length=_MAX_CHANGES
    )
    changed_relationships: tuple[str, ...] = Field(
        default_factory=tuple, max_length=_MAX_CHANGES
    )

    @property
    def equivalent(self) -> bool:
        """Whether the compared snapshots carry no semantic differences."""
        return not (
            self.added_objects
            or self.removed_objects
            or self.changed_objects
            or self.added_fields
            or self.removed_fields
            or self.changed_field_types
            or self.added_constraints
            or self.removed_constraints
            or self.changed_constraints
            or self.added_relationships
            or self.removed_relationships
            or self.changed_relationships
        )

    def safe_payload(self) -> dict[str, Any]:
        """Serialize with safe references only; never raw values."""
        return {
            "added_objects": list(self.added_objects),
            "removed_objects": list(self.removed_objects),
            "changed_objects": [
                change.canonical_payload() for change in self.changed_objects
            ],
            "added_fields": [change.canonical_payload() for change in self.added_fields],
            "removed_fields": [
                change.canonical_payload() for change in self.removed_fields
            ],
            "changed_field_types": [
                change.canonical_payload() for change in self.changed_field_types
            ],
            "added_constraints": list(self.added_constraints),
            "removed_constraints": list(self.removed_constraints),
            "changed_constraints": list(self.changed_constraints),
            "added_relationships": list(self.added_relationships),
            "removed_relationships": list(self.removed_relationships),
            "changed_relationships": list(self.changed_relationships),
        }


def _constraint_signature(constraint: MetadataConstraint) -> tuple[str, ...]:
    """Canonical identity of a constraint's observable semantics."""
    return (constraint.kind.value, *sorted(constraint.fields))


def _relationship_signature(relationship: MetadataRelationship) -> tuple[str, ...]:
    """Canonical identity of a relationship's observable semantics."""
    return (
        relationship.kind.value,
        relationship.source_object_id,
        relationship.target_object_id,
        *sorted(relationship.source_fields),
        *sorted(relationship.target_fields),
    )


def compare_snapshots(before: MetadataSnapshot, after: MetadataSnapshot) -> SnapshotComparison:
    """Compare two snapshots and report bounded safe changes.

    Objects are compared by canonical object id; fields by object id and
    field id.  A field counts as changed when its normalized type differs.
    Constraints and relationships compare by their observable signatures
    (kind and referenced members), so equivalent metadata mapped in a
    different order stays stable.
    """
    before_objects = {obj.object_id: obj for obj in before.objects}
    after_objects = {obj.object_id: obj for obj in after.objects}

    added_objects = sorted(after_objects.keys() - before_objects.keys())
    removed_objects = sorted(before_objects.keys() - after_objects.keys())

    changed_objects: list[ObjectChange] = []
    added_fields: list[ObjectChange] = []
    removed_fields: list[ObjectChange] = []
    changed_field_types: list[FieldTypeChange] = []

    for object_id in sorted(before_objects.keys() & after_objects.keys()):
        before_obj = before_objects[object_id]
        after_obj = after_objects[object_id]
        before_fields = {field.field_id: field for field in before_obj.fields}
        after_fields = {field.field_id: field for field in after_obj.fields}
        added = sorted(after_fields.keys() - before_fields.keys())
        removed = sorted(before_fields.keys() - after_fields.keys())
        if added:
            added_fields.append(ObjectChange(object_id=object_id, field_ids=frozenset(added)))
        if removed:
            removed_fields.append(
                ObjectChange(object_id=object_id, field_ids=frozenset(removed))
            )
        for field_id in sorted(before_fields.keys() & after_fields.keys()):
            before_type = before_fields[field_id].data_type
            after_type = after_fields[field_id].data_type
            if before_type != after_type:
                changed_field_types.append(
                    FieldTypeChange(
                        object_id=object_id,
                        field_id=field_id,
                        before_type=before_type,
                        after_type=after_type,
                    )
                )
        semantic_changed = bool(
            added or removed or any(
                before_fields[field_id].data_type != after_fields[field_id].data_type
                for field_id in before_fields.keys() & after_fields.keys()
            )
        )
        if semantic_changed:
            changed_objects.append(
                ObjectChange(object_id=object_id, field_ids=frozenset(added + removed))
            )

    before_constraints = {
        constraint.constraint_id: constraint
        for obj in before_objects.values()
        for constraint in obj.constraints
    }
    after_constraints = {
        constraint.constraint_id: constraint
        for obj in after_objects.values()
        for constraint in obj.constraints
    }
    before_constraint_signatures = {
        constraint_id: _constraint_signature(constraint)
        for constraint_id, constraint in before_constraints.items()
    }
    after_constraint_signatures = {
        constraint_id: _constraint_signature(constraint)
        for constraint_id, constraint in after_constraints.items()
    }
    added_constraints = sorted(
        after_constraint_signatures.keys() - before_constraint_signatures.keys()
    )
    removed_constraints = sorted(
        before_constraint_signatures.keys() - after_constraint_signatures.keys()
    )
    changed_constraints = sorted(
        constraint_id
        for constraint_id in before_constraint_signatures.keys()
        & after_constraint_signatures.keys()
        if before_constraint_signatures[constraint_id]
        != after_constraint_signatures[constraint_id]
    )

    before_relationships = {
        relationship.relationship_id: relationship for relationship in before.relationships
    }
    after_relationships = {
        relationship.relationship_id: relationship for relationship in after.relationships
    }
    before_relationship_signatures = {
        relationship_id: _relationship_signature(relationship)
        for relationship_id, relationship in before_relationships.items()
    }
    after_relationship_signatures = {
        relationship_id: _relationship_signature(relationship)
        for relationship_id, relationship in after_relationships.items()
    }
    added_relationships = sorted(
        after_relationship_signatures.keys() - before_relationship_signatures.keys()
    )
    removed_relationships = sorted(
        before_relationship_signatures.keys() - after_relationship_signatures.keys()
    )
    changed_relationships = sorted(
        relationship_id
        for relationship_id in before_relationship_signatures.keys()
        & after_relationship_signatures.keys()
        if before_relationship_signatures[relationship_id]
        != after_relationship_signatures[relationship_id]
    )

    return SnapshotComparison(
        added_objects=tuple(added_objects),
        removed_objects=tuple(removed_objects),
        changed_objects=tuple(changed_objects[:_MAX_CHANGES]),
        added_fields=tuple(added_fields[:_MAX_CHANGES]),
        removed_fields=tuple(removed_fields[:_MAX_CHANGES]),
        changed_field_types=tuple(changed_field_types[:_MAX_CHANGES]),
        added_constraints=tuple(added_constraints[:_MAX_CHANGES]),
        removed_constraints=tuple(removed_constraints[:_MAX_CHANGES]),
        changed_constraints=tuple(changed_constraints[:_MAX_CHANGES]),
        added_relationships=tuple(added_relationships[:_MAX_CHANGES]),
        removed_relationships=tuple(removed_relationships[:_MAX_CHANGES]),
        changed_relationships=tuple(changed_relationships[:_MAX_CHANGES]),
    )
