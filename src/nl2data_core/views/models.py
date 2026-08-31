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
from typing import Any, Literal, cast

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

#: Bounded limits for value-semantics members.
_MAX_VALUE_MAPPING_ENTRIES = 4_096
_MAX_VALUE_TERMS = 4_096
_MAX_VALUE_TERM_CHARS = 128

#: Bounded limits for calculated-field members (design D2/D4).
_MAX_CF_DEPTH = 16
_MAX_CF_NODES = 64
_MAX_CALCULATED_FIELDS = 32

#: The closed calculated-field operator whitelist (design D2).
_CALCULATED_FIELD_OPERATORS = frozenset(
    {"field", "const", "add", "sub", "mul", "div"}
)

#: Descriptor data types a calculated-field ``field`` leaf may reference.
_CALCULATED_FIELD_LEAF_TYPES = frozenset({"int", "float"})

#: Declared output types of a calculated field (design D2).
CalculatedOutputType = Literal["int", "float"]


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


class ValueSemantics(BaseModel):
    """Business-word to stored-value semantics for one enum-coded field.

    ``value_mapping`` is the only member that constrains SQL generation:
    it maps business words (keys) to stored values, and intent resolution
    performs a deterministic governed lookup against it.  The reverse
    direction (result interpretation) is an adapter/result-layer
    responsibility, never a semantic-layer one.

    ``sample_values`` is prompt-context enrichment only - it is never a
    SQL constraint, filter expansion, or enum-domain declaration.
    Confusing sample values with the mapping would let non-binding prompt
    hints masquerade as an exhaustive domain.

    The v4.1 value domain is ``str | int`` only: ``float`` is excluded so
    canonical-JSON float representation cannot threaten fingerprint
    stability, and ``bool`` (an ``int`` subclass) is rejected explicitly.
    The member is fully inside the descriptor fingerprint domain and is
    omitted from the canonical payload when unset (invariant N6).  JSON
    -wire safe: ``dict``/``list`` fields only, never a ``frozenset``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    value_mapping: dict[str, str | int] = Field(
        max_length=_MAX_VALUE_MAPPING_ENTRIES
    )
    display_order: tuple[str, ...] | None = Field(
        default=None, max_length=_MAX_VALUE_TERMS
    )
    sample_values: tuple[str | int, ...] | None = Field(
        default=None, max_length=_MAX_VALUE_TERMS
    )
    pii: bool = False
    unknown_value_policy: Literal["reject", "warn"] = "reject"

    @field_validator("value_mapping", mode="before")
    @classmethod
    def _reject_boolean_mapping_values(
        cls, value: Any
    ) -> Any:
        # Pydantic lax coercion would silently turn ``True`` into ``1``
        # before the typed validator runs, so booleans are rejected at
        # the raw-input boundary (bool is an ``int`` subclass).
        if isinstance(value, dict) and any(
            isinstance(mapped, bool) for mapped in value.values()
        ):
            raise ValueError(
                "value_mapping stored values must be str or int; bool and "
                "float are rejected to keep canonical fingerprints stable"
            )
        return value

    @field_validator("value_mapping")
    @classmethod
    def _valid_mapping(
        cls, value: dict[str, str | int]
    ) -> dict[str, str | int]:
        if not value:
            raise ValueError(
                "a provided ValueSemantics must carry a non-empty value_mapping "
                "(set means non-empty)"
            )
        for key, mapped in value.items():
            if not key or len(key) > _MAX_VALUE_TERM_CHARS:
                raise ValueError("value_mapping keys must be 1-128 characters")
            if isinstance(mapped, bool) or not isinstance(mapped, (str, int)):
                raise ValueError(
                    "value_mapping stored values must be str or int; bool and "
                    "float are rejected to keep canonical fingerprints stable"
                )
        return cast(dict[str, str | int], _FrozenDict(value))

    @field_validator("display_order")
    @classmethod
    def _valid_display_order(
        cls, value: tuple[str, ...] | None
    ) -> tuple[str, ...] | None:
        if value is None:
            return None
        for term in value:
            if not term or len(term) > _MAX_VALUE_TERM_CHARS:
                raise ValueError("display_order terms must be 1-128 characters")
        if len(value) != len(set(value)):
            raise ValueError("display_order terms must be unique")
        return value

    @field_validator("sample_values", mode="before")
    @classmethod
    def _reject_boolean_sample_values(cls, value: Any) -> Any:
        if isinstance(value, (list, tuple)) and any(
            isinstance(sample, bool) for sample in value
        ):
            raise ValueError(
                "sample_values must be str or int; bool and float are rejected"
            )
        return value

    @field_validator("sample_values")
    @classmethod
    def _valid_sample_values(
        cls, value: tuple[str | int, ...] | None
    ) -> tuple[str | int, ...] | None:
        if value is None:
            return None
        for sample in value:
            if isinstance(sample, bool) or not isinstance(sample, (str, int)):
                raise ValueError(
                    "sample_values must be str or int; bool and float are rejected"
                )
            if isinstance(sample, str) and len(sample) > _MAX_VALUE_TERM_CHARS:
                raise ValueError("sample_values terms must be 1-128 characters")
        return value

    @property
    def known_business_terms(self) -> tuple[str, ...]:
        """The bounded, sorted business words declared by the mapping."""
        return tuple(sorted(self.value_mapping))

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "value_mapping": dict(sorted(self.value_mapping.items())),
            "display_order": list(self.display_order)
            if self.display_order is not None
            else None,
            "sample_values": list(self.sample_values)
            if self.sample_values is not None
            else None,
            "pii": self.pii,
            "unknown_value_policy": self.unknown_value_policy,
        }


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
    value_semantics: ValueSemantics | None = None

    @field_validator("description")
    @classmethod
    def _safe_description(cls, value: str) -> str:
        return validate_safe_description(value)

    def canonical_payload(self) -> dict[str, Any]:
        # Invariant N6: an optional semantic member that is unset MUST be
        # omitted from the canonical payload so its introduction cannot
        # change the fingerprint of any descriptor that does not use it.
        payload: dict[str, Any] = {
            "field_id": self.field_id,
            "label": self.label,
            "description": self.description,
            "data_type": self.data_type,
            "allowed_aggregations": sorted(self.allowed_aggregations),
        }
        if self.value_semantics is not None:
            payload["value_semantics"] = self.value_semantics.canonical_payload()
        return payload


def _infer_calculated_output(
    node: ExprNode,
    *,
    fields_by_id: dict[str, SemanticFieldDescriptor],
    calculated_names: frozenset[str],
    cf_name: str,
) -> CalculatedOutputType:
    """Infer the output type of a calculated-field expression tree (D2).

    ``add``/``sub``/``mul`` infer ``int`` only when both operands infer
    ``int``, otherwise ``float``; ``div`` always infers ``float``; leaves
    infer their descriptor ``data_type``.  The same walk enforces the
    definition-time reference rules: calculated fields do not compose
    (``CF_002``), referenced fields must exist (``CF_002``), must not be
    ``pii`` (``CF_004``), and must be numeric (``CF_001``).
    """
    if node.op == "const":
        return "int"
    if node.op == "field":
        leaf_id = cast("str", node.field_id)
        if leaf_id in calculated_names:
            raise ValueError(
                f"CF_002: calculated field '{cf_name}' references calculated "
                f"field '{leaf_id}'; calculated fields do not compose"
            )
        field = fields_by_id.get(leaf_id)
        if field is None:
            raise ValueError(
                f"CF_002: calculated field '{cf_name}' references unknown "
                f"field '{leaf_id}'"
            )
        if field.value_semantics is not None and field.value_semantics.pii:
            raise ValueError(
                f"CF_004: calculated field '{cf_name}' references pii field "
                f"'{leaf_id}'"
            )
        if field.data_type not in _CALCULATED_FIELD_LEAF_TYPES:
            raise ValueError(
                f"CF_001: calculated field '{cf_name}' references non-numeric "
                f"field '{leaf_id}' (data_type '{field.data_type}')"
            )
        return cast("CalculatedOutputType", field.data_type)
    left = _infer_calculated_output(
        cast("ExprNode", node.left),
        fields_by_id=fields_by_id,
        calculated_names=calculated_names,
        cf_name=cf_name,
    )
    right = _infer_calculated_output(
        cast("ExprNode", node.right),
        fields_by_id=fields_by_id,
        calculated_names=calculated_names,
        cf_name=cf_name,
    )
    if node.op == "div":
        return "float"
    return "int" if left == "int" and right == "int" else "float"


class ExprNode(BaseModel):
    """One node of a calculated-field expression tree (design D2).

    The grammar is a closed whitelist: leaves are ``field`` (a bounded
    identifier) and ``const`` (``int`` only - ``float`` and ``bool`` are
    rejected so the tree stays inside the fingerprint domain; negative
    ints are valid), and operators are ``add``, ``sub``, ``mul``, and
    ``div`` with exactly two children.  The tree is bounded (depth <= 16,
    node count <= 64), frozen, and JSON-wire safe (canonical payloads are
    nested dict/list only, never a ``frozenset``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    op: str
    field_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    const: int | None = None
    left: ExprNode | None = None
    right: ExprNode | None = None

    @field_validator("const", mode="before")
    @classmethod
    def _int_only_const(cls, value: Any) -> Any:
        # bool is an int subclass and lax coercion would accept integral
        # floats, so the raw-input boundary rejects both explicitly.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(
                "CF_001: const values must be int; float and bool are "
                "rejected to keep expression fingerprints stable"
            )
        return value

    @model_validator(mode="after")
    def _valid_shape_and_bounds(self) -> ExprNode:
        if self.op not in _CALCULATED_FIELD_OPERATORS:
            raise ValueError(
                f"CF_001: operator '{self.op}' is outside the closed "
                "whitelist (field, const, add, sub, mul, div)"
            )
        if self.op == "field":
            if self.field_id is None:
                raise ValueError("CF_001: 'field' leaves require a field_id")
            if (
                self.const is not None
                or self.left is not None
                or self.right is not None
            ):
                raise ValueError(
                    "CF_001: 'field' leaves must not carry const or operands"
                )
        elif self.op == "const":
            if self.const is None:
                raise ValueError(
                    "CF_001: 'const' leaves require an int const value"
                )
            if (
                self.field_id is not None
                or self.left is not None
                or self.right is not None
            ):
                raise ValueError(
                    "CF_001: 'const' leaves must not carry field_id or operands"
                )
        else:
            if self.left is None or self.right is None:
                raise ValueError(
                    f"CF_001: operator '{self.op}' requires exactly two operands"
                )
            if self.field_id is not None or self.const is not None:
                raise ValueError(
                    f"CF_001: operator '{self.op}' must not carry field_id or const"
                )
        depth, nodes = self._measure()
        if depth > _MAX_CF_DEPTH:
            raise ValueError(
                f"CF_001: expression depth {depth} exceeds the maximum "
                f"of {_MAX_CF_DEPTH}"
            )
        if nodes > _MAX_CF_NODES:
            raise ValueError(
                f"CF_001: expression node count {nodes} exceeds the maximum "
                f"of {_MAX_CF_NODES}"
            )
        return self

    def _measure(self) -> tuple[int, int]:
        """The (depth, node count) of the subtree rooted at this node."""
        if self.op in ("field", "const"):
            return 1, 1
        left_depth, left_nodes = cast("ExprNode", self.left)._measure()
        right_depth, right_nodes = cast("ExprNode", self.right)._measure()
        return 1 + max(left_depth, right_depth), 1 + left_nodes + right_nodes

    def field_leaves(self) -> frozenset[str]:
        """The set of field identifiers referenced by this tree."""
        if self.op == "field":
            return frozenset({cast("str", self.field_id)})
        if self.op == "const":
            return frozenset()
        return cast("ExprNode", self.left).field_leaves() | cast(
            "ExprNode", self.right
        ).field_leaves()

    def canonical_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"op": self.op}
        if self.op == "field":
            payload["field_id"] = self.field_id
        elif self.op == "const":
            payload["const"] = self.const
        else:
            payload["left"] = cast("ExprNode", self.left).canonical_payload()
            payload["right"] = cast("ExprNode", self.right).canonical_payload()
        return payload


class CalculatedField(BaseModel):
    """One governed row-level calculated field over an entity's own fields.

    The expression references only base fields of its own entity
    (calculated fields do not compose); the declared ``output_type`` must
    equal the type the inference table derives from the expression tree
    (enforced where the descriptor field types are known); and ``requires``
    is the exact, order-free set of referenced field leaves (``CF_002`` on
    mismatch, set semantics - declaration order is not constrained).
    ``zero_division_policy`` controls runtime division-by-zero behavior:
    ``null`` yields NULL/missing, ``error`` fails execution with the
    structured ``CF_005`` error.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=_IDENTIFIER_PATTERN)
    label: str = Field(min_length=1, max_length=_MAX_LABEL_CHARS)
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_CHARS)
    expression: ExprNode
    output_type: CalculatedOutputType
    requires: tuple[str, ...] = Field(max_length=_MAX_CALCULATED_FIELDS)
    zero_division_policy: Literal["null", "error"] = "null"

    @field_validator("description")
    @classmethod
    def _safe_description(cls, value: str) -> str:
        return validate_safe_description(value)

    @field_validator("requires")
    @classmethod
    def _valid_requires(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for name in value:
            if re.fullmatch(_IDENTIFIER_PATTERN, name) is None:
                raise ValueError(
                    "calculated-field requires entries must be bounded identifiers"
                )
        if len(value) != len(set(value)):
            raise ValueError(
                "CF_002: calculated-field requires entries must be unique"
            )
        return value

    @model_validator(mode="after")
    def _requires_matches_leaves(self) -> CalculatedField:
        if set(self.requires) != self.expression.field_leaves():
            raise ValueError(
                "CF_002: requires must exactly match the set of field leaves "
                "referenced by the expression (order-free)"
            )
        return self

    def content_hash(self) -> str:
        """Safe hash anchor over the complete published definition."""
        return sha256_fingerprint(self.canonical_payload())

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "expression": self.expression.canonical_payload(),
            "output_type": self.output_type,
            "requires": sorted(self.requires),
            "zero_division_policy": self.zero_division_policy,
        }


class SemanticRelationshipDescriptor(BaseModel):
    """One bounded semantic relationship between two entities."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    relationship_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    source_entity_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    target_entity_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    label: str = Field(min_length=1, max_length=_MAX_LABEL_CHARS)
    source_fields: tuple[str, ...] = Field(default_factory=tuple, max_length=_MAX_FIELDS)
    target_fields: tuple[str, ...] = Field(default_factory=tuple, max_length=_MAX_FIELDS)

    @model_validator(mode="after")
    def _valid_join_fields(self) -> SemanticRelationshipDescriptor:
        if bool(self.source_fields) != bool(self.target_fields):
            raise ValueError("relationship join fields must be provided on both sides")
        if len(self.source_fields) != len(self.target_fields):
            raise ValueError("relationship source and target fields must have matching lengths")
        for field_id in (*self.source_fields, *self.target_fields):
            if re.fullmatch(_IDENTIFIER_PATTERN, field_id) is None:
                raise ValueError("relationship join fields must be bounded identifiers")
        if len(self.source_fields) != len(set(self.source_fields)):
            raise ValueError("relationship source fields must be unique")
        if len(self.target_fields) != len(set(self.target_fields)):
            raise ValueError("relationship target fields must be unique")
        return self

    def canonical_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "relationship_id": self.relationship_id,
            "source_entity_id": self.source_entity_id,
            "target_entity_id": self.target_entity_id,
            "label": self.label,
        }
        if self.source_fields:
            payload["source_fields"] = list(self.source_fields)
            payload["target_fields"] = list(self.target_fields)
        return payload


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
    calculated_fields: tuple[CalculatedField, ...] | None = Field(
        default=None, max_length=_MAX_CALCULATED_FIELDS
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

    @model_validator(mode="after")
    def _validate_calculated_fields(self) -> SemanticEntityDescriptor:
        """Entity-level calculated-field rules (design D4).

        A set member must be non-empty; names are unique within the entity
        and must not collide with any field id of the same entity (the
        ``CF_003`` namespace rule); and every expression's declared output
        must equal the inferred output given the entity's own field types.
        """
        if self.calculated_fields is None:
            return self
        if not self.calculated_fields:
            raise ValueError(
                "a provided calculated_fields member must be non-empty "
                "(set means non-empty)"
            )
        names = [calculated.name for calculated in self.calculated_fields]
        if len(names) != len(set(names)):
            raise ValueError(
                "CF_001: calculated field names must be unique within the entity"
            )
        field_ids = {field.field_id for field in self.fields}
        for name in names:
            if name in field_ids:
                raise ValueError(
                    f"CF_001: calculated field name '{name}' collides with a "
                    "field id of the same entity"
                )
        fields_by_id = {field.field_id: field for field in self.fields}
        calculated_names = frozenset(names)
        for calculated in self.calculated_fields:
            inferred = _infer_calculated_output(
                calculated.expression,
                fields_by_id=fields_by_id,
                calculated_names=calculated_names,
                cf_name=calculated.name,
            )
            if calculated.output_type != inferred:
                raise ValueError(
                    f"CF_001: calculated field '{calculated.name}' declares "
                    f"output type '{calculated.output_type}' but the "
                    f"expression infers '{inferred}'"
                )
        return self

    def canonical_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "entity_id": self.entity_id,
            "label": self.label,
            "description": self.description,
            "fields": [field.canonical_payload() for field in self.fields],
            "relationships": [
                relationship.canonical_payload() for relationship in self.relationships
            ],
        }
        # Invariant N6: an optional semantic member that is unset MUST be
        # omitted from the canonical payload so its introduction cannot
        # change the fingerprint of any entity that does not use it.
        if self.calculated_fields is not None:
            payload["calculated_fields"] = [
                calculated.canonical_payload() for calculated in self.calculated_fields
            ]
        return payload

    def calculated_field(self, name: str) -> CalculatedField | None:
        """The calculated field with the given name, or ``None`` when absent."""
        for calculated in self.calculated_fields or ():
            if calculated.name == name:
                return calculated
        return None


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
        calculated_names = [
            calculated.name
            for entity in value
            for calculated in entity.calculated_fields or ()
        ]
        if len(calculated_names) != len(set(calculated_names)):
            raise ValueError(
                "descriptor calculated field names must be unique across entities"
            )
        collisions = sorted(set(calculated_names) & set(field_ids))
        if collisions:
            raise ValueError(
                "descriptor calculated field names must not collide with any "
                f"field id (first collision: '{collisions[0]}')"
            )
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

    def calculated_field(self, name: str) -> CalculatedField | None:
        """The first calculated field with the given name, or ``None``."""
        for entity in self.entities:
            calculated = entity.calculated_field(name)
            if calculated is not None:
                return calculated
        return None

    def all_calculated_field_ids(self) -> frozenset[str]:
        """Every calculated field name declared anywhere in the descriptor."""
        return frozenset(
            calculated.name
            for entity in self.entities
            for calculated in entity.calculated_fields or ()
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
