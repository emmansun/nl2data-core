"""Canonical Semantic Query IR: versioned, immutable, backend-neutral.

The IR is the stable logical boundary between probabilistic intent
interpretation and deterministic compilation (DDS-019).  It carries
selections, filters, grouping, ordering, bounded limits, time context,
result shape, view/source provenance, and capability requirements - never
SQL, MQL, credentials, executable code, native driver objects, or
presentation configuration.  Physical bindings are supplied by the
compiler context, not by the IR payload.

Every value is frozen and rejects unknown fields; filter values are
restricted to JSON scalars (or tuples of scalars), extension payloads must
be JSON-compatible, and all collection sizes are bounded so a malformed
IR cannot grow without limit.  Fingerprints cover the canonical logical
payload including the explicit ``ir_version``, so a version change
invalidates every previously recorded fingerprint.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from math import isfinite
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nl2data_core.canonical import canonical_json, sha256_fingerprint
from nl2data_core.planning.models import AggregationKind, FilterOperator, OrderDirection

IR_VERSION = 1

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

_MAX_SELECTIONS = 1_000
_MAX_FILTERS = 1_000
_MAX_GROUPINGS = 100
_MAX_ORDERINGS = 1_000
_MAX_EXTENSIONS = 64
_MAX_CAPABILITIES = 64
_MAX_LIMIT = 1_000_000
_MAX_JSON_KEYS = 128

_FORBIDDEN_EXTENSION_KEYS = frozenset(
    {
        "sql",
        "mql",
        "query",
        "statement",
        "script",
        "code",
        "credentials",
        "password",
        "secret",
        "token",
        "uri",
        "connection",
        "driver",
        "binding",
        "chart",
        "chart_config",
    }
)
_EXECUTABLE_TEXT = re.compile(
    r"\b(select|insert|update|delete|drop|create|alter|merge|function|eval)\b",
    re.IGNORECASE,
)

#: Public scalar set; anything else is a driver-native value and is rejected.
SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool, type(None))


class _FrozenJSONMapping(dict[str, Any]):
    """Deeply immutable JSON mapping stored by the canonical IR."""

    def _raise_immutable(self) -> None:
        raise TypeError("canonical IR JSON payloads are immutable")

    def __setitem__(self, key: str, value: Any) -> None:
        self._raise_immutable()

    def __delitem__(self, key: str) -> None:
        self._raise_immutable()

    def __ior__(self, value: Any) -> _FrozenJSONMapping:  # type: ignore[override, misc]
        self._raise_immutable()
        raise AssertionError("unreachable")

    def clear(self) -> None:
        self._raise_immutable()

    def pop(self, key: str, default: Any = None) -> Any:
        self._raise_immutable()
        raise AssertionError("unreachable")

    def popitem(self) -> tuple[str, Any]:
        self._raise_immutable()
        raise AssertionError("unreachable")

    def setdefault(self, key: str, default: Any = None) -> Any:
        self._raise_immutable()
        raise AssertionError("unreachable")

    def update(self, *args: Any, **kwargs: Any) -> None:
        self._raise_immutable()


def _freeze_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _FrozenJSONMapping(
            {str(key): _freeze_json_value(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_json_value(item) for item in value)
    return value


def _check_scalar(value: Any) -> None:
    if not isinstance(value, SCALAR_TYPES):
        raise ValueError(
            "IR values must be scalar (str, int, float, bool, None) or a tuple of scalars, "
            f"got {type(value).__name__}"
        )
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("IR floating-point values must be finite")


def _check_json_value(value: Any, path: str) -> None:
    """Reject anything that cannot cross a JSON wire boundary."""
    if isinstance(value, SCALAR_TYPES):
        return
    if isinstance(value, Mapping):
        if len(value) > _MAX_JSON_KEYS:
            raise ValueError(f"{path} exceeds the bounded key count {_MAX_JSON_KEYS}")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} contains a non-string key")
            if key.lower() in _FORBIDDEN_EXTENSION_KEYS:
                raise ValueError(f"{path}.{key} is physical or unsafe payload material")
            _check_json_value(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        if len(value) > _MAX_JSON_KEYS:
            raise ValueError(f"{path} exceeds the bounded item count {_MAX_JSON_KEYS}")
        for index, item in enumerate(value):
            _check_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, str) and _EXECUTABLE_TEXT.search(value):
        raise ValueError(f"{path} contains executable payload material")
    raise ValueError(f"{path} contains a non-JSON-compatible value ({type(value).__name__})")


class IRSelection(BaseModel):
    """One bounded output selection; never a SQL expression."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    selection_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    field_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    alias: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    aggregation: AggregationKind = "none"

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "selection_id": self.selection_id,
            "field_id": self.field_id,
            "alias": self.alias,
            "aggregation": self.aggregation,
        }


class IRFilter(BaseModel):
    """One typed filter with scalar-only values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    filter_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    field_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    operator: FilterOperator
    value: Any = None

    @field_validator("value")
    @classmethod
    def _validate_value(cls, value: Any) -> Any:
        if isinstance(value, (tuple, list)):
            items = tuple(value)
            for item in items:
                _check_scalar(item)
            return items
        _check_scalar(value)
        return value

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "filter_id": self.filter_id,
            "field_id": self.field_id,
            "operator": self.operator,
            "value": self.value,
        }


class IRGrouping(BaseModel):
    """One explicit grouping directive over a selected field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    grouping_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    field_id: str = Field(pattern=_IDENTIFIER_PATTERN)

    def canonical_payload(self) -> dict[str, Any]:
        return {"grouping_id": self.grouping_id, "field_id": self.field_id}


class IROrdering(BaseModel):
    """One bounded ordering directive."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ordering_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    field_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    direction: OrderDirection = "asc"

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "ordering_id": self.ordering_id,
            "field_id": self.field_id,
            "direction": self.direction,
        }


class IRTimeContext(BaseModel):
    """Optional time-reference metadata for the logical request.

    ``as_of`` carries one scalar instant; ``range`` carries a bounded pair
    of scalars.  Actual time boundaries live in filters - this is metadata
    only and never accepts SQL or driver-native time values.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    context_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    reference: Literal["as_of", "range"] = "as_of"
    value: Any = None

    @field_validator("value")
    @classmethod
    def _validate_value(cls, value: Any) -> Any:
        if isinstance(value, (tuple, list)):
            if len(value) != 2:
                raise ValueError("range time context requires exactly two scalar bounds")
            items = tuple(value)
            for item in items:
                _check_scalar(item)
            return items
        _check_scalar(value)
        return value

    @model_validator(mode="after")
    def _validate_reference_shape(self) -> IRTimeContext:
        if self.reference == "range" and not isinstance(self.value, tuple):
            raise ValueError("range time context requires exactly two scalar bounds")
        if self.reference == "as_of" and isinstance(self.value, tuple):
            raise ValueError("as_of time context requires one scalar value")
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "reference": self.reference,
            "value": self.value,
        }


class IRResultShape(BaseModel):
    """Declared logical result shape; consistency is validated structurally."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["rows", "grouped_rows", "scalar"] = "rows"

    def canonical_payload(self) -> dict[str, Any]:
        return {"kind": self.kind}


class IRProvenance(BaseModel):
    """View/source provenance of the logical request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    root_entity_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    catalog_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    policy_view_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "root_entity_id": self.root_entity_id,
            "catalog_fingerprint": self.catalog_fingerprint,
            "policy_view_fingerprint": self.policy_view_fingerprint,
        }


class IRExtension(BaseModel):
    """One explicit extension node.

    Extensions are fail-closed: an extension is only accepted when its
    ``kind`` is declared in the IR's ``required_capabilities``.  The
    payload must be JSON-compatible so native objects or executable code
    can never enter the canonical representation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    extension_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    kind: str = Field(pattern=_IDENTIFIER_PATTERN)
    payload: dict[str, Any] = Field(default_factory=dict, max_length=_MAX_JSON_KEYS)

    @field_validator("payload")
    @classmethod
    def _json_compatible_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        _check_json_value(value, "payload")
        return cast(dict[str, Any], _freeze_json_value(value))

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "extension_id": self.extension_id,
            "kind": self.kind,
            "payload": self.payload,
        }


class SemanticQueryIR(BaseModel):
    """An immutable versioned canonical semantic query.

    The fingerprint is deterministic: equivalent IR values constructed
    with different mapping insertion orders produce the same canonical
    serialization and fingerprint.  Physical bindings, raw SQL/MQL,
    credentials, executable code, native objects, and presentation
    configuration are structurally rejected - no field can carry them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ir_version: Literal[1] = 1
    ir_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    root_entity_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    selections: tuple[IRSelection, ...] = Field(min_length=1, max_length=_MAX_SELECTIONS)
    filters: tuple[IRFilter, ...] = Field(default_factory=tuple, max_length=_MAX_FILTERS)
    groupings: tuple[IRGrouping, ...] = Field(default_factory=tuple, max_length=_MAX_GROUPINGS)
    orderings: tuple[IROrdering, ...] = Field(default_factory=tuple, max_length=_MAX_ORDERINGS)
    limit: int | None = Field(default=None, ge=1, le=_MAX_LIMIT)
    time_context: IRTimeContext | None = None
    result_shape: IRResultShape = Field(default_factory=IRResultShape)
    provenance: IRProvenance
    required_capabilities: tuple[str, ...] = Field(
        default_factory=tuple, max_length=_MAX_CAPABILITIES
    )
    extensions: tuple[IRExtension, ...] = Field(default_factory=tuple, max_length=_MAX_EXTENSIONS)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @field_validator("selections")
    @classmethod
    def _unique_selections(cls, value: tuple[IRSelection, ...]) -> tuple[IRSelection, ...]:
        ids = [selection.selection_id for selection in value]
        if len(ids) != len(set(ids)):
            raise ValueError("selection ids must be unique")
        return value

    @field_validator("required_capabilities")
    @classmethod
    def _valid_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("required capabilities must be unique")
        for capability in value:
            if re.fullmatch(_IDENTIFIER_PATTERN, capability) is None:
                raise ValueError("required capabilities must be bounded identifiers")
        return value

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> SemanticQueryIR:
        fingerprint = sha256_fingerprint(self.canonical_payload())
        object.__setattr__(self, "fingerprint", fingerprint)
        return self

    def canonical_payload(self) -> dict[str, Any]:
        """The canonical logical payload; excludes the fingerprint itself."""
        return {
            "ir_version": self.ir_version,
            "ir_id": self.ir_id,
            "source_id": self.source_id,
            "root_entity_id": self.root_entity_id,
            "selections": [selection.canonical_payload() for selection in self.selections],
            "filters": [filter_.canonical_payload() for filter_ in self.filters],
            "groupings": [grouping.canonical_payload() for grouping in self.groupings],
            "orderings": [ordering.canonical_payload() for ordering in self.orderings],
            "limit": self.limit,
            "time_context": (
                self.time_context.canonical_payload() if self.time_context is not None else None
            ),
            "result_shape": self.result_shape.canonical_payload(),
            "provenance": self.provenance.canonical_payload(),
            "required_capabilities": sorted(self.required_capabilities),
            "extensions": [extension.canonical_payload() for extension in self.extensions],
        }

    def serialize_canonical(self) -> str:
        """Canonical JSON with explicit version and sorted keys."""
        return canonical_json(self.canonical_payload())

    @classmethod
    def from_canonical_json(cls, payload: str) -> SemanticQueryIR:
        """Load an IR from its canonical JSON form.

        The fingerprint is recomputed from the canonical payload, so an
        altered fingerprint in the input can never be trusted.
        """
        return cls.model_validate_json(payload)

    def field_ids(self) -> frozenset[str]:
        """All semantic field ids referenced by the IR."""
        return (
            frozenset(selection.field_id for selection in self.selections)
            | frozenset(filter_.field_id for filter_ in self.filters)
            | frozenset(grouping.field_id for grouping in self.groupings)
            | frozenset(ordering.field_id for ordering in self.orderings)
        )

    def filter_fingerprints(self) -> frozenset[str]:
        """Stable fingerprints of every filter in the IR.

        The fingerprint is computed from the semantic facts only (field,
        operator, value) and matches the legacy plan filter fingerprints,
        so governance obligations stay stable across the migration.
        """
        return frozenset(
            sha256_fingerprint(
                {
                    "field_id": filter_.field_id,
                    "operator": filter_.operator,
                    "value": filter_.value,
                }
            )
            for filter_ in self.filters
        )
