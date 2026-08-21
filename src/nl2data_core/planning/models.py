"""Immutable backend-neutral Semantic Query Plan models.

The plan references semantic IDs, source and catalog/policy fingerprints,
bounded selections, filters, ordering, and lineage.  It never embeds SQL
syntax, SQL AST nodes, or driver-native values; those are rejected by the
strict scalar typing below and by :mod:`nl2data_core.planning.validation`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nl2data_core.canonical import sha256_fingerprint

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_MAX_PLAN_LIMIT = 1_000_000

AggregationKind = Literal["none", "count", "sum", "avg", "min", "max"]
FilterOperator = Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains"]
OrderDirection = Literal["asc", "desc"]

#: Public scalar set; anything else is a driver-native value and is rejected.
SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool, type(None))
_SCALAR_TYPE_NAMES = "str, int, float, bool, None"


def _check_scalar(value: Any) -> None:
    if not isinstance(value, SCALAR_TYPES):
        raise ValueError(
            f"filter values must be scalar ({_SCALAR_TYPE_NAMES}) or a tuple of scalars, "
            f"got {type(value).__name__}"
        )


class SemanticSelection(BaseModel):
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


class SemanticFilter(BaseModel):
    """One typed filter with a stable canonical fingerprint.

    The fingerprint is computed from the semantic facts only (field,
    operator, value) and is what governance obligations are bound to.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    filter_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    field_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    operator: FilterOperator
    value: Any = None
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @field_validator("value")
    @classmethod
    def _validate_value(cls, value: Any) -> Any:
        if isinstance(value, tuple):
            for item in value:
                _check_scalar(item)
            return value
        _check_scalar(value)
        return value

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> SemanticFilter:
        fingerprint = sha256_fingerprint(
            {
                "field_id": self.field_id,
                "operator": self.operator,
                "value": self.value,
            }
        )
        object.__setattr__(self, "fingerprint", fingerprint)
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "filter_id": self.filter_id,
            "field_id": self.field_id,
            "operator": self.operator,
            "value": self.value,
        }


class SemanticOrdering(BaseModel):
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


class ColumnBinding(BaseModel):
    """Physical column binding for one semantic field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    physical_name: str = Field(pattern=_IDENTIFIER_PATTERN)


class PhysicalBinding(BaseModel):
    """Minimal physical binding used to compile the first plan cases.

    Contains physical names only - never SQL AST nodes or driver objects.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    object_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    dialect: str = Field(min_length=1, max_length=32)
    column_bindings: tuple[ColumnBinding, ...] = Field(default_factory=tuple)

    def physical_name(self, field_id: str) -> str | None:
        for binding in self.column_bindings:
            if binding.field_id == field_id:
                return binding.physical_name
        return None

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "dialect": self.dialect,
            "column_bindings": [binding.model_dump() for binding in self.column_bindings],
        }


class PlanLineage(BaseModel):
    """Source and catalog lineage of a plan."""

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


class SemanticQueryPlan(BaseModel):
    """An immutable executable analytical request, backend-neutral.

    The plan fingerprint is deterministic: equivalent plans built with
    different mapping insertion orders produce the same fingerprint.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    root_entity_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    selections: tuple[SemanticSelection, ...] = Field(min_length=1, max_length=1_000)
    filters: tuple[SemanticFilter, ...] = Field(default_factory=tuple, max_length=1_000)
    orderings: tuple[SemanticOrdering, ...] = Field(default_factory=tuple, max_length=1_000)
    limit: int | None = Field(default=None, ge=1, le=_MAX_PLAN_LIMIT)
    lineage: PlanLineage
    binding: PhysicalBinding | None = None
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @field_validator("selections")
    @classmethod
    def _unique_selections(
        cls, value: tuple[SemanticSelection, ...]
    ) -> tuple[SemanticSelection, ...]:
        ids = [selection.selection_id for selection in value]
        if len(ids) != len(set(ids)):
            raise ValueError("selection ids must be unique")
        return value

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> SemanticQueryPlan:
        fingerprint = sha256_fingerprint(
            {
                "plan_id": self.plan_id,
                "source_id": self.source_id,
                "root_entity_id": self.root_entity_id,
                "selections": [selection.canonical_payload() for selection in self.selections],
                "filters": [filter_.canonical_payload() for filter_ in self.filters],
                "orderings": [ordering.canonical_payload() for ordering in self.orderings],
                "limit": self.limit,
                "lineage": self.lineage.canonical_payload(),
                "binding": (self.binding.canonical_payload() if self.binding is not None else None),
            }
        )
        object.__setattr__(self, "fingerprint", fingerprint)
        return self

    def field_ids(self) -> frozenset[str]:
        """All semantic field ids referenced by the plan."""
        return (
            frozenset(selection.field_id for selection in self.selections)
            | frozenset(filter_.field_id for filter_ in self.filters)
            | frozenset(ordering.field_id for ordering in self.orderings)
        )

    def filter_fingerprints(self) -> frozenset[str]:
        """Stable fingerprints of every filter in the plan."""
        return frozenset(filter_.fingerprint for filter_ in self.filters)


class PlanValidationIssue(BaseModel):
    """One structured validation issue found in a plan."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=512)
    path: str | None = Field(default=None, max_length=256)


class PlanValidationResult(BaseModel):
    """Result of validating a plan before any adapter is invoked."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    plan_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    issues: tuple[PlanValidationIssue, ...] = Field(default_factory=tuple)

    def issue_codes(self) -> list[str]:
        return [issue.code for issue in self.issues]


def validate_plan_structure(plan: SemanticQueryPlan) -> PlanValidationResult:
    """Structural invariant checks shared by every planner path.

    Checks are independent of any catalog or policy view; view-scoped
    checks are performed by :func:`nl2data_core.planning.validation
    .validate_plan_against_view`.
    """
    issues: list[PlanValidationIssue] = []
    if plan.lineage.source_id != plan.source_id:
        issues.append(
            PlanValidationIssue(
                code="source_mismatch",
                message="lineage source does not match plan source",
                path="lineage.source_id",
            )
        )
    if plan.lineage.root_entity_id != plan.root_entity_id:
        issues.append(
            PlanValidationIssue(
                code="root_entity_mismatch",
                message="lineage root entity does not match plan root entity",
                path="lineage.root_entity_id",
            )
        )
    if plan.binding is not None:
        for selection in plan.selections:
            if plan.binding.physical_name(selection.field_id) is None:
                issues.append(
                    PlanValidationIssue(
                        code="unbound_selection",
                        message=f"selection references unbound field '{selection.field_id}'",
                        path=f"binding.{selection.selection_id}",
                    )
                )
    return PlanValidationResult(
        valid=not issues,
        plan_fingerprint=plan.fingerprint,
        issues=tuple(issues),
    )
