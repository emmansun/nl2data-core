"""Deterministic conversion of the supported Wren MDL subset.

Mapping contract (documented normatively in the authoring guide):

- Models map to authoring entities; plain columns map to fields.
  Identities are resolved through the optional override manifest first,
  then by deterministic normalization; unresolvable identities and
  collisions fail with stable codes instead of being renamed silently.
- Model ``refSql``/``tableReference`` and plain-column ``expression``
  values are physical provenance only: they establish that the column
  exists but are never copied into the semantic-only authoring output.
  A plain-column ``expression`` must be a single physical identifier
  token; anything else is executable SQL and is rejected.
- Columns flagged ``isCalculated`` must express exactly an ``ExprNode``
  tree (``+ - * /`` over integer-typed same-entity field leaves and
  integer constants); dotted or SQL-bearing expressions, and
  non-integer leaves, are rejected.  Field
  leaves are mapped to the target field identifiers.
- Columns with a ``relationship`` reference are navigation sugar: the
  referenced relationship must exist and the column is not emitted as
  a field.
- Relationships map when the join type is directional (``ONE_TO_ONE``,
  ``ONE_TO_MANY``, ``MANY_TO_ONE``), the two model references resolve,
  and the condition is a chain of simple equalities between plain
  columns of the two models.  The relationship is attached to the
  entity of the first listed model.
- A metric maps only when it declares exactly one measure of the form
  ``agg(Model.field)`` with a supported aggregation and no dimensions,
  time grains, filter, or caching; everything else is rejected.
- Unknown members anywhere fail closed.  Empty containers for members
  the schema tolerates carry no semantics and are accepted.

Conversion never infers physical deployment bindings, credentials,
tenant context, or lifecycle state, and never echoes rejected input
values into diagnostics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, Literal

from pydantic import ValidationError

from nl2data_core.assembly.authoring.models import (
    AUTHORING_API_VERSION,
    AUTHORING_KIND,
    AuthoringCalculatedField,
    AuthoringEntity,
    AuthoringField,
    AuthoringMeasure,
    AuthoringMetadata,
    AuthoringRelationship,
    AuthoringSource,
    AuthoringSpec,
    SemanticAssemblyAuthoring,
)
from nl2data_core.views.models import ExprNode

from .diagnostics import DiagnosticSink, MdlDiagnostic
from .manifest import MappingManifest

#: Maximum label/description lengths the authoring models accept.
_MAX_LABEL_CHARS = 256
_MAX_DESCRIPTION_CHARS = 1_024

IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-.]{0,127}$")
_PHYSICAL_COLUMN_RE = re.compile(r"^[A-Za-z0-9_]+$")
_AGG_EXPRESSION_RE = re.compile(
    r"^(sum|avg|min|max|count)\(\s*([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\s*\)$"
)
_EQUALITY_RE = re.compile(
    r"^([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\s*=\s*([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)$"
)
_AND_SPLIT_RE = re.compile(r"\s+AND\s+", re.IGNORECASE)
_TOKEN_RE = re.compile(r"\s*(?:(\d+)|([A-Za-z_][A-Za-z0-9_]*)|([-+*/()]))")

_MODEL_MEMBERS = frozenset(
    {"name", "refSql", "tableReference", "columns", "primaryKey", "description", "properties"}
)
_COLUMN_MEMBERS = frozenset(
    {"name", "type", "expression", "isCalculated", "relationship", "description", "properties"}
)
_RELATIONSHIP_MEMBERS = frozenset({"name", "models", "joinType", "condition", "properties"})
_METRIC_MEMBERS = frozenset(
    {
        "name",
        "description",
        "measures",
        "dimensions",
        "timeGrains",
        "cached",
        "filter",
        "properties",
    }
)
_MEASURE_MEMBERS = frozenset({"name", "expression"})

_SUPPORTED_JOIN_TYPES = frozenset({"ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_ONE"})
_OPERATOR_NAMES = {"+": "add", "-": "sub", "*": "mul", "/": "div"}

#: Parenthesis nesting bound for calculated expressions; matches the
#: governed ``ExprNode`` depth bound so the recursive-descent parser
#: stops before either Python or the governed model would.
_MAX_EXPRESSION_DEPTH = 16

#: Documented mapping from Wren MDL physical type names to the bounded
#: authoring data-type vocabulary.  Unmapped types keep their normalized
#: identifier so plain fields can still carry dialect-specific types.
_TYPE_VOCABULARY = {
    "int": "int",
    "integer": "int",
    "bigint": "int",
    "smallint": "int",
    "tinyint": "int",
    "double": "float",
    "float": "float",
    "real": "float",
    "decimal": "float",
    "numeric": "float",
    "varchar": "str",
    "char": "str",
    "string": "str",
    "text": "str",
    "bool": "bool",
    "boolean": "bool",
    "date": "date",
    "time": "time",
    "timestamp": "timestamp",
}
_CALCULATED_LEAF_TYPES = frozenset({"int", "float"})

_IMPORT_DESCRIPTION = "Imported from a Wren MDL document."


@dataclass
class _ModelPlan:
    """Intermediate, order-independent plan for one MDL model."""

    index: int
    name: str
    entity_id: str
    description: str
    fields: list[AuthoringField] = dataclass_field(default_factory=list)
    calculated: list[tuple[str, dict[str, Any], tuple[str | int, ...]]] = (
        dataclass_field(default_factory=list)
    )
    relationship_refs: list[str] = dataclass_field(default_factory=list)
    column_to_field: dict[str, str] = dataclass_field(default_factory=dict)
    field_types: dict[str, str] = dataclass_field(default_factory=dict)


@dataclass
class ConvertOutcome:
    """In-memory authoring model plus bounded diagnostics."""

    model: SemanticAssemblyAuthoring | None
    diagnostics: tuple[MdlDiagnostic, ...]
    truncated: bool = False

    @property
    def converted(self) -> bool:
        return self.model is not None and not self.diagnostics


def convert_mdl(
    document: dict[str, Any],
    *,
    source_id: str,
    bundle_id: str,
    model_version: str,
    manifest: MappingManifest | None = None,
) -> ConvertOutcome:
    """Convert one parsed MDL document into an authoring model."""
    sink = DiagnosticSink()
    overrides = manifest or MappingManifest()

    if not all(
        IDENTIFIER_RE.match(identity) for identity in (source_id, bundle_id, model_version)
    ):
        sink.add(
            "missing_mapping",
            (),
            "source, bundle, and model-version identities must be valid "
            "authoring identifiers",
        )
        return _finish(sink, None)

    plans = _plan_models(sink, document, overrides)
    if plans is None:
        return _finish(sink, None)

    relationships = _convert_relationships(sink, document, overrides, plans)
    if relationships is None:
        return _finish(sink, None)
    if not _validate_relationship_columns(sink, plans, set(relationships[1])):
        return _finish(sink, None)

    calculated = _build_calculated_fields(sink, plans)
    if calculated is None:
        return _finish(sink, None)

    measures = _convert_metrics(sink, document, overrides, plans)
    if measures is None:
        return _finish(sink, None)

    entities = _assemble_entities(sink, plans, relationships[0], calculated)
    if entities is None:
        return _finish(sink, None)

    try:
        model = SemanticAssemblyAuthoring(
            apiVersion=AUTHORING_API_VERSION,
            kind=AUTHORING_KIND,
            metadata=AuthoringMetadata(
                bundle_id=bundle_id,
                model_version=model_version,
                description=_IMPORT_DESCRIPTION,
            ),
            spec=AuthoringSpec(
                source=AuthoringSource(source_id=source_id),
                entities=tuple(sorted(entities, key=lambda entity: entity.entity_id)),
                measures=tuple(sorted(measures, key=lambda measure: measure.measure_id)),
            ),
        )
    except ValidationError:
        sink.add(
            "unsupported_construct",
            (),
            "a source label or description is not representable in the "
            "authoring contract",
        )
        return _finish(sink, None)
    return _finish(sink, model)


def _finish(sink: DiagnosticSink, model: SemanticAssemblyAuthoring | None) -> ConvertOutcome:
    if sink.items:
        return ConvertOutcome(
            model=None,
            diagnostics=tuple(
                sorted(sink.items, key=lambda item: (item.path, item.code, item.message))
            ),
            truncated=sink.truncated,
        )
    return ConvertOutcome(model=model, diagnostics=())


def _label(source_name: str, identifier: str) -> str:
    """Deterministic bounded label for an imported identity."""
    if 0 < len(source_name) <= _MAX_LABEL_CHARS:
        return source_name
    return identifier


def normalize_identifier(name: str) -> str | None:
    """Normalize an MDL name to an authoring identifier, or None."""
    if IDENTIFIER_RE.match(name):
        return name
    candidate = re.sub(r"[^A-Za-z0-9_\-.]+", "_", name)
    return candidate if IDENTIFIER_RE.match(candidate) else None


def _resolve_identity(
    sink: DiagnosticSink,
    path: tuple[str | int, ...],
    section: str,
    source_name: str,
    overrides: MappingManifest,
    claimed: dict[str, str],
) -> str | None:
    """Resolve one identity through the manifest, then normalization."""
    identifier = overrides.identifier_for(section, source_name)
    if identifier is None:
        identifier = normalize_identifier(source_name)
    if identifier is None:
        sink.add(
            "missing_mapping",
            path,
            "this name cannot be normalized to an identifier; supply an "
            "explicit override",
        )
        return None
    owner = claimed.get(identifier)
    if owner is not None:
        sink.add(
            "identifier_collision",
            path,
            "the normalized identity collides with another member; supply "
            "an explicit override",
        )
        return None
    claimed[identifier] = source_name
    return identifier


def _plan_models(
    sink: DiagnosticSink,
    document: dict[str, Any],
    overrides: MappingManifest,
) -> list[_ModelPlan] | None:
    """Scan models into an intermediate plan; no authoring models yet."""
    entity_claimed: dict[str, str] = {}
    field_claimed: dict[str, str] = {}

    plans: list[_ModelPlan] = []
    for model_index, raw_model in enumerate(document["models"]):
        model_path: tuple[str | int, ...] = ("models", model_index)
        if sorted(set(raw_model) - _MODEL_MEMBERS):
            sink.add(
                "unsupported_construct",
                model_path,
                "this model member is not part of the supported subset",
            )
            continue

        name = raw_model.get("name")
        if not isinstance(name, str) or not name:
            sink.add("missing_mapping", model_path, "every model requires a string name")
            return None
        entity_id = _resolve_identity(
            sink, model_path, "entities", name, overrides, entity_claimed
        )
        if entity_id is None:
            return None

        description = raw_model.get("description", "")
        if not isinstance(description, str):
            sink.add(
                "unsupported_construct",
                (*model_path, "description"),
                "the model description must be a string",
            )
            return None
        if raw_model.get("properties", {}) not in ({}, None):
            sink.add(
                "unsupported_construct",
                (*model_path, "properties"),
                "only empty model properties are tolerated",
            )
            return None

        columns = raw_model.get("columns")
        if not isinstance(columns, list) or not columns:
            sink.add(
                "missing_mapping",
                (*model_path, "columns"),
                "every model requires at least one column",
            )
            return None

        plan = _ModelPlan(
            index=model_index,
            name=name,
            entity_id=entity_id,
            description=description,
        )

        primary_key = raw_model.get("primaryKey")
        if primary_key is not None and not isinstance(primary_key, str):
            sink.add(
                "unsupported_construct",
                (*model_path, "primaryKey"),
                "the primary key must be a column name",
            )
            return None

        for column_index, raw_column in enumerate(columns):
            if not _plan_column(
                sink,
                (*model_path, "columns", column_index),
                plan,
                raw_column,
                overrides,
                field_claimed,
            ):
                return None

        if primary_key is not None and primary_key not in plan.column_to_field:
            sink.add(
                "missing_mapping",
                (*model_path, "primaryKey"),
                "the primary key does not resolve to a plain column of this "
                "model",
            )
            return None

        plans.append(plan)
    return plans


def _plan_column(
    sink: DiagnosticSink,
    column_path: tuple[str | int, ...],
    plan: _ModelPlan,
    raw_column: Any,
    overrides: MappingManifest,
    field_claimed: dict[str, str],
) -> bool:
    if not isinstance(raw_column, dict) or sorted(set(raw_column) - _COLUMN_MEMBERS):
        sink.add(
            "unsupported_construct",
            column_path,
            "this column member is not part of the supported subset",
        )
        return False

    name = raw_column.get("name")
    if not isinstance(name, str) or not name:
        sink.add("missing_mapping", column_path, "every column requires a string name")
        return False

    description = raw_column.get("description", "")
    if not isinstance(description, str) or len(description) > _MAX_DESCRIPTION_CHARS:
        sink.add(
            "unsupported_construct",
            (*column_path, "description"),
            "the column description is not representable",
        )
        return False
    if raw_column.get("properties", {}) not in ({}, None):
        sink.add(
            "unsupported_construct",
            (*column_path, "properties"),
            "only empty column properties are tolerated",
        )
        return False

    is_calculated = raw_column.get("isCalculated", False)
    if not isinstance(is_calculated, bool):
        sink.add(
            "unsupported_construct",
            (*column_path, "isCalculated"),
            "isCalculated must be a boolean",
        )
        return False

    relationship_name = raw_column.get("relationship")
    if relationship_name is not None:
        if not isinstance(relationship_name, str) or not relationship_name:
            sink.add(
                "unsupported_construct",
                (*column_path, "relationship"),
                "the relationship reference must be a relationship name",
            )
            return False
        if is_calculated:
            sink.add(
                "unsupported_construct",
                column_path,
                "a column cannot be both a relationship and a calculation",
            )
            return False
        if raw_column.get("expression") is not None:
            sink.add(
                "unsupported_construct",
                (*column_path, "expression"),
                "a relationship column must not carry a SQL expression",
            )
            return False
        plan.relationship_refs.append(relationship_name)
        return True  # navigation sugar; not a semantic field

    column_type = _convert_type(sink, (*column_path, "type"), raw_column.get("type"))
    if column_type is None:
        return False

    expression = raw_column.get("expression")
    if is_calculated:
        if not isinstance(expression, str) or not expression.strip():
            sink.add(
                "missing_mapping",
                (*column_path, "expression"),
                "a calculated column requires an expression",
            )
            return False
        field_id = overrides.identifier_for("calculated", f"{plan.name}.{name}")
        if field_id is None:
            field_id = normalize_identifier(name)
        if field_id is None:
            sink.add(
                "missing_mapping",
                column_path,
                "this calculated name cannot be normalized to an identifier; "
                "supply an explicit override",
            )
            return False
        owner = field_claimed.get(field_id)
        if owner is not None:
            sink.add(
                "identifier_collision",
                column_path,
                "the normalized identity collides with another field; supply "
                "an explicit override",
            )
            return False
        field_claimed[field_id] = f"{plan.name}.{name}"
        plan.calculated.append((field_id, raw_column, column_path))
        return True  # emitted as a calculated field, not a plain field

    if expression is not None and (
        not isinstance(expression, str) or not _PHYSICAL_COLUMN_RE.fullmatch(expression)
    ):
        sink.add(
            "unsupported_construct",
            (*column_path, "expression"),
            "a plain column expression must be a single physical column "
            "name; executable SQL is not representable",
        )
        return False

    field_id = overrides.identifier_for("fields", f"{plan.name}.{name}")
    if field_id is None:
        field_id = normalize_identifier(name)
    if field_id is None:
        sink.add(
            "missing_mapping",
            column_path,
            "this column name cannot be normalized to an identifier; supply "
            "an explicit override",
        )
        return False
    owner = field_claimed.get(field_id)
    if owner is not None:
        sink.add(
            "identifier_collision",
            column_path,
            "the normalized identity collides with another field; supply an "
            "explicit override",
        )
        return False
    field_claimed[field_id] = f"{plan.name}.{name}"
    try:
        plan.fields.append(
            AuthoringField(
                field_id=field_id,
                label=_label(name, field_id),
                description=description,
                data_type=column_type,
            )
        )
    except ValidationError:
        sink.add(
            "unsupported_construct",
            column_path,
            "a column label or type is not representable in the authoring "
            "contract",
        )
        return False
    plan.column_to_field[name] = field_id
    plan.field_types[field_id] = column_type
    return True


def _convert_type(
    sink: DiagnosticSink,
    type_path: tuple[str | int, ...],
    value: Any,
) -> str | None:
    if not isinstance(value, str) or not value.strip():
        sink.add(
            "missing_mapping",
            type_path,
            "every column requires a supported data type",
        )
        return None
    lowered = value.strip().lower()
    mapped = _TYPE_VOCABULARY.get(lowered)
    if mapped is not None:
        return mapped
    normalized = normalize_identifier(lowered)
    if normalized is None:
        sink.add(
            "missing_mapping",
            type_path,
            "this data type cannot be mapped to a semantic data type",
        )
        return None
    return normalized


def _convert_relationships(
    sink: DiagnosticSink,
    document: dict[str, Any],
    overrides: MappingManifest,
    plans: list[_ModelPlan],
) -> tuple[list[tuple[AuthoringRelationship, str, str]], set[str]] | None:
    """Return (attachments, declared names) for all mappable relationships.

    Each attachment is ``(relationship, source_entity_id,
    target_entity_id)``; attachment to the owning entity happens during
    assembly.
    """
    plans_by_name = {plan.name: plan for plan in plans}
    relationship_claimed: dict[str, str] = {}
    attachments: list[tuple[AuthoringRelationship, str, str]] = []
    declared_names: set[str] = set()

    for index, raw_relationship in enumerate(document.get("relationships", [])):
        path: tuple[str | int, ...] = ("relationships", index)
        if not isinstance(raw_relationship, dict) or sorted(
            set(raw_relationship) - _RELATIONSHIP_MEMBERS
        ):
            sink.add(
                "unsupported_construct",
                path,
                "this relationship member is not part of the supported subset",
            )
            continue

        name = raw_relationship.get("name")
        if not isinstance(name, str) or not name:
            sink.add("missing_mapping", path, "every relationship requires a string name")
            return None
        declared_names.add(name)
        relationship_id = _resolve_identity(
            sink, path, "relationships", name, overrides, relationship_claimed
        )
        if relationship_id is None:
            return None

        members = raw_relationship.get("models")
        if (
            not isinstance(members, list)
            or len(members) != 2
            or not all(isinstance(item, str) for item in members)
        ):
            sink.add(
                "unsupported_construct",
                (*path, "models"),
                "a relationship must list exactly two model names",
            )
            return None
        source_model, target_model = members
        source_plan = plans_by_name.get(source_model)
        target_plan = plans_by_name.get(target_model)
        if source_plan is None or target_plan is None:
            sink.add(
                "missing_mapping",
                (*path, "models"),
                "a relationship endpoint does not resolve to a declared model",
            )
            return None

        join_type = raw_relationship.get("joinType")
        if join_type not in _SUPPORTED_JOIN_TYPES:
            sink.add(
                "unsupported_construct",
                (*path, "joinType"),
                "this join type is not representable as a semantic "
                "relationship",
            )
            return None

        if raw_relationship.get("properties", {}) not in ({}, None):
            sink.add(
                "unsupported_construct",
                (*path, "properties"),
                "only empty relationship properties are tolerated",
            )
            return None

        condition = raw_relationship.get("condition")
        if not isinstance(condition, str) or not condition.strip():
            sink.add(
                "missing_mapping",
                (*path, "condition"),
                "a relationship requires a join condition",
            )
            return None
        pairs = _parse_condition(
            sink, path, condition, source_plan, target_plan
        )
        if pairs is None:
            return None

        source_fields = tuple(field for field, _ in pairs)
        target_fields = tuple(field for _, field in pairs)
        try:
            relationship = AuthoringRelationship(
                relationship_id=relationship_id,
                target_entity_id=target_plan.entity_id,
                source_fields=source_fields,
                target_fields=target_fields,
                label=_label(name, relationship_id),
            )
        except ValidationError:
            sink.add(
                "unsupported_construct",
                path,
                "this relationship is not representable as a semantic "
                "relationship",
            )
            return None
        attachments.append((relationship, source_plan.entity_id, target_plan.entity_id))
    return attachments, declared_names


def _parse_condition(
    sink: DiagnosticSink,
    path: tuple[str | int, ...],
    condition: str,
    source_plan: _ModelPlan,
    target_plan: _ModelPlan,
) -> list[tuple[str, str]] | None:
    """Parse an equality chain into (source_field, target_field) pairs."""
    pairs: list[tuple[str, str]] = []
    for part in _AND_SPLIT_RE.split(condition.strip()):
        match = _EQUALITY_RE.fullmatch(part.strip())
        if match is None:
            sink.add(
                "unsupported_construct",
                (*path, "condition"),
                "a join condition must be a chain of simple field equalities",
            )
            return None
        left_model, left_field, right_model, right_field = match.groups()
        left_side = (left_model, left_field)
        right_side = (right_model, right_field)
        models = {left_model, right_model}
        if models != {source_plan.name, target_plan.name}:
            sink.add(
                "unsupported_construct",
                (*path, "condition"),
                "a join equality must reference exactly the two relationship "
                "models",
            )
            return None
        if left_model == right_model:
            sink.add(
                "unsupported_construct",
                (*path, "condition"),
                "a self-referencing join is not representable",
            )
            return None
        for model, column in (left_side, right_side):
            plan = source_plan if model == source_plan.name else target_plan
            if column not in plan.column_to_field:
                sink.add(
                    "missing_mapping",
                    (*path, "condition"),
                    "a join equality references a column the model does not "
                    "declare as a plain field",
                )
                return None
        source_column = left_field if left_model == source_plan.name else right_field
        target_column = right_field if left_model == source_plan.name else left_field
        pairs.append(
            (
                source_plan.column_to_field[source_column],
                target_plan.column_to_field[target_column],
            )
        )
    return pairs


def _validate_relationship_columns(
    sink: DiagnosticSink,
    plans: list[_ModelPlan],
    declared_names: set[str],
) -> bool:
    """Every relationship column must reference a declared relationship."""
    for plan in plans:
        for relationship_name in plan.relationship_refs:
            if relationship_name not in declared_names:
                sink.add(
                    "missing_mapping",
                    ("models", plan.index),
                    "a relationship column references a relationship the "
                    "document does not declare",
                )
                return False
    return True


def _build_calculated_fields(
    sink: DiagnosticSink,
    plans: list[_ModelPlan],
) -> dict[str, list[AuthoringCalculatedField]] | None:
    built: dict[str, list[AuthoringCalculatedField]] = {}
    for plan in plans:
        entries: list[AuthoringCalculatedField] = []
        for calculated_id, raw_column, column_path in plan.calculated:
            expression = _parse_expression(
                sink, plan, str(raw_column["expression"]), (*column_path, "expression")
            )
            if expression is None:
                return None
            non_int_leaves = sorted(
                leaf
                for leaf in expression.field_leaves()
                if plan.field_types.get(leaf) not in _CALCULATED_LEAF_TYPES
            )
            if non_int_leaves:
                sink.add(
                    "unsupported_construct",
                    (*column_path, "expression"),
                    "a calculated field may only reference numeric fields",
                )
                return None

            # The governed contract infers the output type from the tree
            # and the mapped leaf types; the MDL declared type is not used.
            output_type = _infer_output(expression, plan.field_types)

            description = raw_column.get("description", "")
            try:
                entries.append(
                    AuthoringCalculatedField(
                        name=calculated_id,
                        label=_label(str(raw_column["name"]), calculated_id),
                        description=description,
                        expression=expression,
                        output_type=output_type,
                        requires=tuple(sorted(expression.field_leaves())),
                    )
                )
            except ValidationError:
                sink.add(
                    "unsupported_expression",
                    column_path,
                    "the calculated expression exceeds the governed "
                    "expression bounds",
                )
                return None
        built[plan.entity_id] = entries
    return built


def _infer_output(
    node: ExprNode,
    field_types: dict[str, str],
) -> Literal["int", "float"]:
    """Infer the calculated output type exactly like the bundle oracle.

    Callers guard the leaves to the numeric vocabulary first, so only the
    governed output types can be produced here.
    """
    if node.op == "const":
        return "int"
    if node.op == "field":
        return "int" if field_types.get(str(node.field_id), "int") == "int" else "float"
    # The governed grammar guarantees both operands on binary operators.
    assert node.left is not None and node.right is not None
    left = _infer_output(node.left, field_types)
    right = _infer_output(node.right, field_types)
    if node.op == "div":
        return "float"
    return "int" if left == "int" and right == "int" else "float"


def _parse_expression(
    sink: DiagnosticSink,
    plan: _ModelPlan,
    text: str,
    path: tuple[str | int, ...],
) -> ExprNode | None:
    tokens: list[str] = []
    position = 0
    while position < len(text):
        if text[position:].strip() == "":
            break
        match = _TOKEN_RE.match(text, position)
        if match is None:
            sink.add(
                "unsupported_expression",
                path,
                "the calculated expression uses syntax outside the governed "
                "expression grammar",
            )
            return None
        position = match.end()
        number, identifier, operator = match.groups()
        tokens.append(number or identifier or operator)

    parser = _ExpressionParser(sink, plan, tokens, path)
    try:
        node = parser.parse_expression()
    except ValidationError:
        # The governed tree enforces its own depth/node-count bounds
        # while the parser builds it; anything beyond them is not
        # representable and fails the conversion.
        sink.add(
            "unsupported_expression",
            path,
            "the calculated expression exceeds the governed expression "
            "bounds",
        )
        return None
    if node is None:
        return None
    if parser.position != len(tokens):
        sink.add(
            "unsupported_expression",
            path,
            "the calculated expression uses syntax outside the governed "
            "expression grammar",
        )
        return None
    return node


class _ExpressionParser:
    """Recursive-descent parser for the governed ExprNode grammar.

    Field leaves are mapped to the target field identifiers of the
    owning model; a leaf that does not resolve to a plain column of the
    model fails the conversion.
    """

    def __init__(
        self,
        sink: DiagnosticSink,
        plan: _ModelPlan,
        tokens: list[str],
        path: tuple[str | int, ...],
    ) -> None:
        self.sink = sink
        self.plan = plan
        self.tokens = tokens
        self.path = path
        self.position = 0
        self.depth = 0

    def fail(self) -> None:
        self.sink.add(
            "unsupported_expression",
            self.path,
            "the calculated expression uses syntax outside the governed "
            "expression grammar",
        )

    def peek(self) -> str | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self) -> str | None:
        token = self.peek()
        if token is not None:
            self.position += 1
        return token

    def parse_expression(self) -> ExprNode | None:
        node = self.parse_term()
        while node is not None and self.peek() in ("+", "-"):
            operator = self.take()
            right = self.parse_term()
            if right is None:
                return None
            node = ExprNode(op=_OPERATOR_NAMES[str(operator)], left=node, right=right)
        return node

    def parse_term(self) -> ExprNode | None:
        node = self.parse_factor()
        while node is not None and self.peek() in ("*", "/"):
            operator = self.take()
            right = self.parse_factor()
            if right is None:
                return None
            node = ExprNode(op=_OPERATOR_NAMES[str(operator)], left=node, right=right)
        return node

    def parse_factor(self) -> ExprNode | None:
        token = self.take()
        if token is None:
            self.fail()
            return None
        if token == "(":
            if self.depth >= _MAX_EXPRESSION_DEPTH:
                self.fail()
                return None
            self.depth += 1
            node = self.parse_expression()
            if node is None or self.take() != ")":
                self.fail()
                return None
            self.depth -= 1
            return node
        if token == "-":
            operand = self.take()
            if operand is None or not operand.isdigit():
                self.fail()
                return None
            return ExprNode(op="const", const=-int(operand))
        if token.isdigit():
            return ExprNode(op="const", const=int(token))
        if _PHYSICAL_COLUMN_RE.fullmatch(token) and not token[0].isdigit():
            field_id = self.plan.column_to_field.get(token)
            if field_id is None:
                self.sink.add(
                    "missing_mapping",
                    self.path,
                    "a calculated expression references a column the model "
                    "does not declare as a plain field",
                )
                return None
            return ExprNode(op="field", field_id=field_id)
        self.fail()
        return None


def _assemble_entities(
    sink: DiagnosticSink,
    plans: list[_ModelPlan],
    attachments: list[tuple[AuthoringRelationship, str, str]],
    calculated: dict[str, list[AuthoringCalculatedField]],
) -> list[AuthoringEntity] | None:
    entities: list[AuthoringEntity] = []
    for plan in plans:
        relationships = tuple(
            sorted(
                (
                    relationship
                    for relationship, source_id, _ in attachments
                    if source_id == plan.entity_id
                ),
                key=lambda item: item.relationship_id,
            )
        )
        try:
            entities.append(
                AuthoringEntity(
                    entity_id=plan.entity_id,
                    label=_label(plan.name, plan.entity_id),
                    description=plan.description,
                    fields=tuple(sorted(plan.fields, key=lambda item: item.field_id)),
                    relationships=relationships,
                    calculated_fields=tuple(
                        sorted(calculated.get(plan.entity_id, []), key=lambda item: item.name)
                    ),
                )
            )
        except ValidationError:
            sink.add(
                "unsupported_construct",
                ("models", plan.index),
                "a model label or description is not representable in the "
                "authoring contract",
            )
            return None
    return entities


def _convert_metrics(
    sink: DiagnosticSink,
    document: dict[str, Any],
    overrides: MappingManifest,
    plans: list[_ModelPlan],
) -> list[AuthoringMeasure] | None:
    plans_by_name = {plan.name: plan for plan in plans}
    measure_claimed: dict[str, str] = {}
    measures: list[AuthoringMeasure] = []

    for index, raw_metric in enumerate(document.get("metrics", [])):
        path: tuple[str | int, ...] = ("metrics", index)
        if not isinstance(raw_metric, dict) or sorted(set(raw_metric) - _METRIC_MEMBERS):
            sink.add(
                "unsupported_construct",
                path,
                "this metric member is not part of the supported subset",
            )
            continue
        for member, empty in (
            ("dimensions", []),
            ("timeGrains", []),
            ("cached", {}),
            ("properties", {}),
        ):
            if member in raw_metric and raw_metric[member] not in (empty, None):
                sink.add(
                    "unsupported_construct",
                    (*path, member),
                    "only empty content is tolerated for this metric member",
                )
                return None
        if raw_metric.get("filter") is not None:
            sink.add(
                "unsupported_construct",
                (*path, "filter"),
                "metric filters are not representable",
            )
            return None

        name = raw_metric.get("name")
        if not isinstance(name, str) or not name:
            sink.add("missing_mapping", path, "every metric requires a string name")
            return None
        measure_id = overrides.identifier_for("measures", name)
        if measure_id is None:
            measure_id = normalize_identifier(name)
        if measure_id is None:
            sink.add(
                "missing_mapping",
                path,
                "this metric name cannot be normalized to an identifier; "
                "supply an explicit override",
            )
            return None
        owner = measure_claimed.get(measure_id)
        if owner is not None:
            sink.add(
                "identifier_collision",
                path,
                "the normalized identity collides with another measure; "
                "supply an explicit override",
            )
            return None
        measure_claimed[measure_id] = name

        metric_measures = raw_metric.get("measures")
        if not isinstance(metric_measures, list) or len(metric_measures) != 1:
            sink.add(
                "unsupported_construct",
                (*path, "measures"),
                "only a metric with exactly one measure is representable",
            )
            return None
        raw_measure = metric_measures[0]
        if (
            not isinstance(raw_measure, dict)
            or set(raw_measure) - _MEASURE_MEMBERS
            or not isinstance(raw_measure.get("name"), str)
        ):
            sink.add(
                "unsupported_construct",
                (*path, "measures"),
                "this measure member is not part of the supported subset",
            )
            return None
        match = _AGG_EXPRESSION_RE.fullmatch(str(raw_measure.get("expression", "")))
        if match is None:
            sink.add(
                "unsupported_expression",
                (*path, "measures"),
                "a measure expression must aggregate exactly one model field",
            )
            return None
        aggregation, model_name, column_name = match.groups()
        plan = plans_by_name.get(model_name)
        field_id = plan.column_to_field.get(column_name) if plan is not None else None
        if field_id is None:
            sink.add(
                "missing_mapping",
                (*path, "measures"),
                "a measure references a model field that the document does "
                "not map",
            )
            return None

        description = raw_metric.get("description", "")
        if not isinstance(description, str) or len(description) > _MAX_DESCRIPTION_CHARS:
            sink.add(
                "unsupported_construct",
                (*path, "description"),
                "the metric description is not representable",
            )
            return None
        try:
            measures.append(
                AuthoringMeasure(
                    measure_id=measure_id,
                    field_id=field_id,
                    aggregation=aggregation,  # type: ignore[arg-type]
                    label=_label(name, measure_id),
                    description=description,
                )
            )
        except ValidationError:
            sink.add(
                "unsupported_construct",
                path,
                "this metric is not representable as an authoring measure",
            )
            return None
    return measures
