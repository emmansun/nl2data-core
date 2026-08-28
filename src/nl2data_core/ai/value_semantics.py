"""Deterministic value-semantics resolution for intent filters (v4.1).

Implements design decisions D8-D10 of the ``semantic-value-semantics``
change: mapping lookups read the bundle-referenced descriptor snapshot
(never a live registry; unavailable snapshot fails closed), stored values
pass through under type-strict membership, mapped fields accept only
``eq``/``in``, and every filter value produces a bounded outcome on the
resolution outcome channel (design D9).  Deterministic lookup against a
governed mapping is not planner construction (restated N4); probabilistic
value construction remains rejected.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from nl2data_core.ai.errors import (
    ModelErrorCode,
    ModelErrorRecord,
    ModelInvocationError,
)
from nl2data_core.ai.models import (
    FilterResolutionOutcome,
    FilterValueOutcome,
    IntentFilter,
    ValueResolutionOutcome,
)
from nl2data_core.views.models import ValueSemantics

#: v4.1 operator whitelist for fields with declared value semantics.
ALLOWED_VALUE_OPERATORS = frozenset({"eq", "in"})

#: Bounded number of known business terms surfaced by a VS_001 error.
_MAX_KNOWN_TERMS = 32

_CANONICAL_INT = re.compile(r"-?\d+")

_MISSING = object()


def vs_001_error(
    *,
    field_id: str,
    attempted_value: Any,
    known_business_terms: tuple[str, ...],
) -> ModelErrorRecord:
    """Structured VS_001 unknown-value resolution failure.

    The record exposes only the attempted business value and the known
    business terms - never physical names or the mapping's stored values.
    """
    terms = ",".join(known_business_terms[:_MAX_KNOWN_TERMS])
    return ModelInvocationError(
        ModelErrorCode.VALUE_UNKNOWN,
        "filter value is not a known business term for this field",
        details={
            "field": field_id,
            "attempted_value": _scalar_text(attempted_value),
            "known_business_terms": terms,
        },
    ).to_record()


def vs_002_error(
    *,
    field_id: str,
    attempted_operator: str,
) -> ModelErrorRecord:
    """Structured VS_002 disallowed-operator resolution failure."""
    return ModelInvocationError(
        ModelErrorCode.VALUE_OPERATOR_DISALLOWED,
        "fields with value semantics accept only eq and in filters",
        details={
            "field": field_id,
            "attempted_operator": attempted_operator,
            "allowed_operators": "eq,in",
        },
    ).to_record()


def snapshot_unavailable_error() -> ModelErrorRecord:
    """Fail-closed error for an unavailable bundle-referenced snapshot."""
    return ModelInvocationError(
        ModelErrorCode.VALUE_SNAPSHOT_UNAVAILABLE,
        "the descriptor snapshot referenced by the active bundle is "
        "unavailable; value resolution fails closed",
    ).to_record()


def _scalar_text(value: Any) -> str:
    """Bounded scalar rendering for error details (never physical names)."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return repr(value)[:64]
    if isinstance(value, str):
        return value[:64]
    return "non-scalar"


def _canonicalize(value: Any, stored_values: frozenset[Any]) -> Any:
    """Canonicalize a wire value to the mapping's declared domain type.

    Type-strict membership (design D10): the value is explicitly
    canonicalized to the declared domain type before comparison and never
    silently coerced; a value that cannot be uniquely canonicalized is a
    miss (prevents a stringified code from entering ``WHERE status = '4'``).
    """
    if isinstance(value, bool):
        return _MISSING
    stored = tuple(stored_values)
    if not stored:
        return _MISSING
    declared_int = all(isinstance(item, int) for item in stored)
    declared_str = all(isinstance(item, str) for item in stored)
    if declared_int:
        if isinstance(value, int):
            return value
        if isinstance(value, str) and _CANONICAL_INT.fullmatch(value):
            canonical = int(value)
            if str(canonical) == value:
                return canonical
            return _MISSING
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return _MISSING
    if declared_str:
        return value if isinstance(value, str) else _MISSING
    # Mixed-domain mapping: only exact type matches are comparable.
    if isinstance(value, (str, int)):
        return value
    return _MISSING


def resolve_single_value(
    semantics: ValueSemantics, value: Any
) -> tuple[str, Any]:
    """Resolve one filter value against one declared mapping.

    Returns ``(status, resolved_value)`` where status is ``hit`` (the
    business word resolved), ``pass_through`` (a governed stored value
    accepted by type-strict membership), or ``miss`` (out of domain; the
    caller applies the unknown-value policy).
    """
    stored_values = frozenset(semantics.value_mapping.values())
    if isinstance(value, str) and value in semantics.value_mapping:
        return "hit", semantics.value_mapping[value]
    canonical = _canonicalize(value, stored_values)
    if canonical is not _MISSING and canonical in stored_values:
        return "pass_through", canonical
    return "miss", value


def _dedup_values(values: tuple[Any, ...]) -> tuple[Any, ...]:
    """Order-preserving dedup keyed by (type, value) to keep the frozen IR
    fingerprint canonical and construction-path-independent."""
    seen: dict[tuple[str, Any], Any] = {}
    for value in values:
        seen.setdefault((type(value).__name__, value), value)
    return tuple(seen.values())


def resolve_intent_filters(
    filters: tuple[IntentFilter, ...],
    semantics_for: Callable[[str], ValueSemantics | None],
) -> tuple[tuple[IntentFilter, ...], ValueResolutionOutcome, ModelErrorRecord | None]:
    """Resolve every filter's values before the IR freezes.

    Returns ``(filters, outcome, error)``.  ``error`` is non-``None`` when
    resolution must fail closed (VS_001 unknown value under the reject
    policy or VS_002 disallowed operator); the caller must not emit an IR.
    Fields without declared value semantics are untouched (``unpolicied``
    outcomes only).  A warn-policy miss proceeds with the original value
    and a ``warned`` outcome.  ``in`` lists resolve per value with
    duplicates removed before the IR freezes; a per-value miss under the
    reject policy fails the whole filter.
    """
    new_filters: list[IntentFilter] = []
    filter_outcomes: list[FilterResolutionOutcome] = []
    for filter_ in filters:
        semantics = semantics_for(filter_.field_id)
        if semantics is None:
            scalar_count = (
                len(filter_.value) if isinstance(filter_.value, tuple) else 1
            )
            outcomes = tuple(
                FilterValueOutcome(
                    filter_id=filter_.filter_id,
                    field_id=filter_.field_id,
                    value_index=index,
                    status="unpolicied",
                )
                for index in range(scalar_count)
            )
            filter_outcomes.append(
                FilterResolutionOutcome(
                    filter_id=filter_.filter_id,
                    field_id=filter_.field_id,
                    operator=filter_.operator,
                    values=outcomes,
                )
            )
            new_filters.append(filter_)
            continue
        if filter_.operator not in ALLOWED_VALUE_OPERATORS:
            # The failing filter occurrence is still recorded (with no
            # per-value outcomes) so the channel stays complete (D9).
            outcome = ValueResolutionOutcome(
                filters=tuple(
                    [
                        *filter_outcomes,
                        FilterResolutionOutcome(
                            filter_id=filter_.filter_id,
                            field_id=filter_.field_id,
                            operator=filter_.operator,
                            values=(),
                        ),
                    ]
                ),
            )
            return (
                tuple(new_filters),
                outcome,
                vs_002_error(
                    field_id=filter_.field_id,
                    attempted_operator=filter_.operator,
                ),
            )
        scalar_values: tuple[Any, ...] = (
            filter_.value
            if isinstance(filter_.value, tuple)
            else (filter_.value,)
        )
        resolved: list[Any] = []
        per_filter_outcomes: list[FilterValueOutcome] = []
        for index, raw in enumerate(scalar_values):
            status, resolved_value = resolve_single_value(semantics, raw)
            if status == "miss":
                if semantics.unknown_value_policy == "reject":
                    # Earlier values of this filter keep their outcomes and
                    # the failing value is recorded as a miss (D9: one
                    # outcome per filter value, even on the failure path).
                    per_filter_outcomes.append(
                        FilterValueOutcome(
                            filter_id=filter_.filter_id,
                            field_id=filter_.field_id,
                            value_index=index,
                            status="miss",
                        )
                    )
                    filter_outcomes.append(
                        FilterResolutionOutcome(
                            filter_id=filter_.filter_id,
                            field_id=filter_.field_id,
                            operator=filter_.operator,
                            values=tuple(per_filter_outcomes),
                        )
                    )
                    outcome = ValueResolutionOutcome(
                        filters=tuple(filter_outcomes),
                    )
                    return (
                        tuple(new_filters),
                        outcome,
                        vs_001_error(
                            field_id=filter_.field_id,
                            attempted_value=raw,
                            known_business_terms=semantics.known_business_terms,
                        ),
                    )
                status = "warned"
                resolved.append(raw)
            else:
                resolved.append(resolved_value)
            per_filter_outcomes.append(
                FilterValueOutcome(
                    filter_id=filter_.filter_id,
                    field_id=filter_.field_id,
                    value_index=index,
                    status=status,  # type: ignore[arg-type]
                )
            )
        if filter_.operator == "in":
            new_value: Any = _dedup_values(tuple(resolved))
        elif isinstance(filter_.value, tuple):
            new_value = _dedup_values(tuple(resolved))
        else:
            new_value = resolved[0] if resolved else None
        try:
            new_filters.append(
                filter_.model_copy(update={"value": new_value})
            )
        except Exception:
            # Resolved values are drawn from the declared str|int domain,
            # so construction failure is a fail-closed boundary.
            outcome = ValueResolutionOutcome(filters=tuple(filter_outcomes))
            return (
                tuple(new_filters),
                outcome,
                vs_001_error(
                    field_id=filter_.field_id,
                    attempted_value=filter_.value,
                    known_business_terms=semantics.known_business_terms,
                ),
            )
        filter_outcomes.append(
            FilterResolutionOutcome(
                filter_id=filter_.filter_id,
                field_id=filter_.field_id,
                operator=filter_.operator,
                values=tuple(per_filter_outcomes),
            )
        )
    snapshot_outcome = ValueResolutionOutcome(filters=tuple(filter_outcomes))
    return tuple(new_filters), snapshot_outcome, None
