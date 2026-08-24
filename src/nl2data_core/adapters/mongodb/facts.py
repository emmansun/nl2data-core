"""Query-fact extraction from one validated MQL spec.

Facts are adapter-neutral: collections, canonical dotted field paths,
operators, pipeline stages, result shape, filter fingerprints, and tenant
routing references.  Native driver objects and raw values never appear in
the extracted facts.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nl2data_core.governance.models import GovernanceFacts

from .models import MongoOperation, MongoQueryFacts, MongoQuerySpec
from .normalize import predicate_fingerprint


def _walk_filter(
    filter_: Mapping[str, Any],
    *,
    fields: set[str],
    operators: set[str],
    fingerprints: set[str],
) -> None:
    """Collect field paths, operators, and leaf predicate fingerprints."""
    for path, value in filter_.items():
        fields.add(path)
        if isinstance(value, Mapping) and value and all(
            str(key).startswith("$") for key in value
        ):
            for operator, operand in value.items():
                operators.add(str(operator))
                fingerprints.add(predicate_fingerprint(path, str(operator), operand))
        elif isinstance(value, Mapping):
            _walk_filter(
                value,
                fields=fields,
                operators=operators,
                fingerprints=fingerprints,
            )
        else:
            operators.add("$eq")
            fingerprints.add(predicate_fingerprint(path, "$eq", value))


def _walk_group(
    argument: Mapping[str, Any],
    *,
    fields: set[str],
    operators: set[str],
) -> None:
    """Collect $group key paths and accumulator expressions."""
    group_id = argument.get("_id")
    if isinstance(group_id, str) and group_id.startswith("$"):
        fields.add(group_id[1:])
    for alias, expression in argument.items():
        if alias == "_id" or not isinstance(expression, Mapping):
            continue
        expr_name, expr_value = next(iter(expression.items()))
        operators.add(str(expr_name))
        if isinstance(expr_value, str) and expr_value.startswith("$"):
            fields.add(expr_value[1:])


def extract_query_facts(spec: MongoQuerySpec, *, source_id: str) -> MongoQueryFacts:
    """Extract adapter-neutral facts from one validated MQL spec."""
    fields: set[str] = set()
    operators: set[str] = set()
    fingerprints: set[str] = set()
    stages: set[str] = set()

    _walk_filter(
        spec.filter, fields=fields, operators=operators, fingerprints=fingerprints
    )
    fields.update(spec.projection)
    fields.update(spec.sort)

    if spec.pipeline is not None:
        for stage in spec.pipeline:
            name, argument = next(iter(stage.items()))
            stages.add(name)
            if name == "$match" and isinstance(argument, Mapping):
                _walk_filter(
                    argument,
                    fields=fields,
                    operators=operators,
                    fingerprints=fingerprints,
                )
            elif name in {"$project", "$sort"} and isinstance(argument, Mapping):
                fields.update(argument)
            elif name == "$group" and isinstance(argument, Mapping):
                _walk_group(argument, fields=fields, operators=operators)
            elif name == "$unwind":
                if isinstance(argument, str) and argument.startswith("$"):
                    fields.add(argument[1:])
                elif (
                    isinstance(argument, Mapping)
                    and isinstance(argument.get("path"), str)
                    and argument["path"].startswith("$")
                ):
                    fields.add(argument["path"][1:])

    return MongoQueryFacts(
        source_id=source_id,
        collection=spec.collection,
        operation=spec.operation,
        field_ids=frozenset(fields),
        operators=frozenset(operators),
        stages=frozenset(stages),
        result_shape="count" if spec.operation == MongoOperation.COUNT else "documents",
        filter_fingerprints=frozenset(fingerprints),
        tenant_obligation_fingerprint=(
            spec.tenant_obligation.fingerprint
            if spec.tenant_obligation is not None
            else None
        ),
        routing_kind=(
            spec.routing_evidence.kind.value
            if spec.routing_evidence is not None
            else None
        ),
        routing_reference=(
            spec.routing_evidence.reference
            if spec.routing_evidence is not None
            else None
        ),
    )


def facts_to_governance(facts: MongoQueryFacts) -> GovernanceFacts:
    """Map adapter facts to the common governance fact contract.

    MongoDB reads are governed as ``select`` operations over the collection
    resource with the referenced dotted fields; filter references are the
    same stable fingerprints the authorization layer binds obligations to.
    """
    return GovernanceFacts(
        source_id=facts.source_id,
        operation="select",
        resource_ids=frozenset({facts.collection}),
        field_ids=facts.field_ids,
        filter_fingerprints=facts.filter_fingerprints,
    )
