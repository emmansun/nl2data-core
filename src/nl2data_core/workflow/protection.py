"""Public result protection applied before ``QueryResult`` construction.

Adapter output is converted into the protected public contract only after
scalar-only rows, bounded columns/rows, and the authorized field scope are
enforced.  Any violation becomes a structured :class:`ResultProtectionError`
so native values or out-of-scope columns can never cross the public
boundary.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError
from nl2data.models import QueryResult
from nl2data_core.adapters.models import ExecutionResult
from nl2data_core.governance.models import EffectiveLimits
from nl2data_core.planning.models import SemanticQueryPlan

#: The protected public scalar set; anything else is unsupported.
_SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool, type(None))


def _result_size_bytes(columns: tuple[str, ...], rows: tuple[tuple[Any, ...], ...]) -> int:
    return sum(len(column.encode("utf-8")) for column in columns) + sum(
        len(str(cell).encode("utf-8")) for row in rows for cell in row
    )


class ResultProtectionError(NL2DataError):
    """Raised when adapter output cannot be protected at the public boundary."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.GOVERNANCE,
            ErrorCode.RESULT_PROTECTION_FAILED,
            message,
            retryable=False,
            details=details,
        )


def _allowed_output_columns(plan: SemanticQueryPlan) -> frozenset[str]:
    """The only column names the plan may expose, from its selections."""
    allowed: set[str] = set()
    for selection in plan.selections:
        if selection.alias is not None:
            allowed.add(selection.alias)
            continue
        if plan.binding is not None:
            physical = plan.binding.physical_name(selection.field_id)
            if physical is not None:
                if selection.aggregation != "none":
                    allowed.add(f"{selection.aggregation}_{physical}")
                else:
                    allowed.add(physical)
    return frozenset(allowed)


def protect_result(
    result: ExecutionResult,
    *,
    plan: SemanticQueryPlan,
    limits: EffectiveLimits,
) -> QueryResult:
    """Convert adapter output into a protected public result.

    Scalar-only rows, bounded columns/rows, and field scope are enforced
    before ``QueryResult`` construction; violations fail as structured
    errors instead of leaking native values.
    """
    if result.row_count > limits.max_rows:
        raise ResultProtectionError(
            "result row count exceeds the authorized bound",
            details={
                "max_rows": str(limits.max_rows),
                "actual": str(result.row_count),
            },
        )
    if len(result.columns) > limits.max_columns:
        raise ResultProtectionError(
            "result column count exceeds the authorized bound",
            details={
                "max_columns": str(limits.max_columns),
                "actual": str(len(result.columns)),
            },
        )
    result_size_bytes = _result_size_bytes(result.columns, result.rows)
    if result_size_bytes > limits.max_result_bytes:
        raise ResultProtectionError(
            "result bytes exceed the authorized maximum",
            details={
                "max_result_bytes": str(limits.max_result_bytes),
                "actual": str(result_size_bytes),
            },
        )
    if result.duration_ms > int(limits.max_execution_seconds * 1000):
        raise ResultProtectionError(
            "result execution exceeded the authorized timeout",
            details={
                "max_execution_seconds": str(limits.max_execution_seconds),
                "actual_duration_ms": str(result.duration_ms),
            },
        )
    for row_index, row in enumerate(result.rows):
        for column_index, cell in enumerate(row):
            if not isinstance(cell, _SCALAR_TYPES):
                raise ResultProtectionError(
                    "result contains a value outside the protected scalar set",
                    details={
                        "row_index": str(row_index),
                        "column_index": str(column_index),
                        "value_type": type(cell).__name__,
                    },
                )

    allowed = _allowed_output_columns(plan)
    keep = [index for index, column in enumerate(result.columns) if column in allowed]
    if not keep and result.columns:
        raise ResultProtectionError(
            "result has no columns inside the authorized field scope",
            details={"column_count": str(len(result.columns))},
        )
    columns = tuple(result.columns[index] for index in keep)
    rows = tuple(tuple(row[index] for index in keep) for row in result.rows)

    try:
        return QueryResult(
            result_id=result.result_id,
            fingerprint=result.fingerprint,
            column_names=columns,
            rows=rows,
        )
    except ValidationError as error:
        raise ResultProtectionError(
            "result could not be protected into the public scalar contract",
            details={"cause_type": type(error).__name__},
        ) from error
