"""Plan invariant validation applied before any adapter is invoked.

These checks reject plans that embed SQL syntax, lack a bounded result
where one is required, leave time boundaries unresolved, or reference
semantic ids outside the authorized view.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    PlanValidationIssue,
    PlanValidationResult,
    SemanticFilter,
    SemanticQueryPlan,
)

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"

#: Time-sensitive fields must always carry a concrete scalar boundary.
_TIME_FIELD_PREFIXES = ("created_at", "updated_at", "occurred_at", "time_")


class AuthorizedView(BaseModel):
    """The authorized semantic view a plan may reference."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    root_entity_ids: frozenset[str] = Field(default_factory=frozenset)
    field_ids: frozenset[str] = Field(default_factory=frozenset)
    catalog_fingerprint: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    def contains_field(self, field_id: str) -> bool:
        return field_id in self.field_ids


def _is_time_field(field_id: str) -> bool:
    return field_id.startswith(_TIME_FIELD_PREFIXES) or field_id.endswith("_at")


def _filter_has_concrete_boundary(filter_: SemanticFilter) -> bool:
    if filter_.operator not in {"eq", "gt", "gte", "lt", "lte", "in", "not_in"}:
        return False
    if isinstance(filter_.value, tuple):
        return all(item is not None for item in filter_.value)
    return filter_.value is not None


def validate_plan_against_view(
    plan: SemanticQueryPlan,
    *,
    view: AuthorizedView | None = None,
    require_bounded: bool = True,
    max_limit: int = 1_000_000,
    required_time_fields: frozenset[str] = frozenset(),
) -> PlanValidationResult:
    """Validate plan invariants against an optional authorized view.

    ``require_bounded`` makes an unbounded plan invalid wherever a bounded
    result is required (the P1 SQL path always requires one).
    """
    issues: list[PlanValidationIssue] = []

    if not plan.source_id:
        issues.append(
            PlanValidationIssue(
                code="missing_source",
                message="plan has no source identity",
                path="source_id",
            )
        )
    if require_bounded and plan.limit is None:
        issues.append(
            PlanValidationIssue(
                code="unbounded_limit",
                message="a bounded result is required but the plan has no limit",
                path="limit",
            )
        )
    elif plan.limit is not None and plan.limit > max_limit:
        issues.append(
            PlanValidationIssue(
                code="limit_exceeds_max",
                message=f"plan limit {plan.limit} exceeds maximum {max_limit}",
                path="limit",
            )
        )

    if view is not None:
        if plan.source_id != view.source_id:
            issues.append(
                PlanValidationIssue(
                    code="source_out_of_scope",
                    message=f"source '{plan.source_id}' is outside the authorized view",
                    path="source_id",
                )
            )
        if view.root_entity_ids and plan.root_entity_id not in view.root_entity_ids:
            issues.append(
                PlanValidationIssue(
                    code="entity_out_of_scope",
                    message=f"root entity '{plan.root_entity_id}' is outside the authorized view",
                    path="root_entity_id",
                )
            )
        for selection in plan.selections:
            if not view.contains_field(selection.field_id):
                issues.append(
                    PlanValidationIssue(
                        code="field_out_of_scope",
                        message=(
                            f"selection field '{selection.field_id}' is outside the authorized view"
                        ),
                        path=f"selections.{selection.selection_id}",
                    )
                )
        for filter_ in plan.filters:
            if not view.contains_field(filter_.field_id):
                issues.append(
                    PlanValidationIssue(
                        code="field_out_of_scope",
                        message=(
                            f"filter field '{filter_.field_id}' is outside the authorized view"
                        ),
                        path=f"filters.{filter_.filter_id}",
                    )
                )
        for ordering in plan.orderings:
            if not view.contains_field(ordering.field_id):
                issues.append(
                    PlanValidationIssue(
                        code="field_out_of_scope",
                        message=(
                            f"ordering field '{ordering.field_id}' is outside the authorized view"
                        ),
                        path=f"orderings.{ordering.ordering_id}",
                    )
                )

    resolved_time_fields: set[str] = set()
    for filter_ in plan.filters:
        if _is_time_field(filter_.field_id) and _filter_has_concrete_boundary(filter_):
            resolved_time_fields.add(filter_.field_id)
    for field_id in sorted(required_time_fields):
        if field_id not in resolved_time_fields:
            issues.append(
                PlanValidationIssue(
                    code="unresolved_time_boundary",
                    message=f"required time field '{field_id}' has no resolved boundary",
                    path=f"filters.{field_id}",
                )
            )
    # Any time-sensitive field referenced with a non-concrete value is unresolved.
    for filter_ in plan.filters:
        if _is_time_field(filter_.field_id) and not _filter_has_concrete_boundary(filter_):
            issues.append(
                PlanValidationIssue(
                    code="unresolved_time_boundary",
                    message=f"time field '{filter_.field_id}' has no concrete boundary value",
                    path=f"filters.{filter_.filter_id}",
                )
            )

    return PlanValidationResult(
        valid=not issues,
        plan_fingerprint=plan.fingerprint,
        issues=tuple(issues),
    )
