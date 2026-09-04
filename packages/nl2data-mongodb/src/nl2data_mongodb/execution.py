"""Bounded MongoDB execution with protected scalar result mapping.

Execution bounds documents, columns, result bytes, and wall-clock time;
normalizes every supported BSON/scalar cell into the protected public
scalar set; and rejects unsupported native values as safe structured
failures - the native value never crosses the boundary.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from nl2data_core.adapters.models import ExecutionResult
from nl2data_core.canonical import strict_sha256_fingerprint
from nl2data_core.compilation.expansion import ZeroDivisionPolicyError

from .executor import MongoExecutor
from .models import MongoExecutionError, MongoOperation, MongoQuerySpec

#: The protected public scalar set; everything else needs explicit support.
_SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool, type(None))

#: The server error code a MongoDB driver reports for ``can't $divide by``
#: ``zero``; it is the only server failure translated into a structured
#: calculated-field error (``CF_005``) rather than a generic execution one.
_DIVIDE_BY_ZERO_CODE = "16608"

_MISSING = object()


def _execute_stage(executor: MongoExecutor, spec: MongoQuerySpec) -> tuple[dict[str, Any], ...]:
    """Run the backend dispatch, translating the divide-by-zero server code."""
    try:
        if spec.operation == MongoOperation.FIND:
            return executor.find_documents(
                collection=spec.collection,
                filter_=spec.filter,
                projection=spec.projection,
                sort=spec.sort,
                skip=spec.skip,
                limit=spec.limit,
            )
        if spec.operation == MongoOperation.AGGREGATE:
            pipeline = spec.pipeline or ()
            if spec.limit is not None:
                pipeline = pipeline + ({"$limit": spec.limit},)
            return executor.aggregate_documents(
                collection=spec.collection,
                pipeline=pipeline,
            )
        if spec.operation == MongoOperation.COUNT:
            count = executor.count_documents(
                collection=spec.collection,
                filter_=spec.filter,
            )
            return ({"count": count},)
    except MongoExecutionError as error:
        if (error.details or {}).get("server_error_code") == _DIVIDE_BY_ZERO_CODE:
            raise ZeroDivisionPolicyError(
                "calculated field division hit a zero denominator under "
                "zero_division_policy: error",
                details={"server_error_code": _DIVIDE_BY_ZERO_CODE},
            ) from error
        raise
    raise MongoExecutionError(
        f"operation '{spec.operation.value}' is not executable",
        details={"operation": spec.operation.value},
    )


def normalize_bson_cell(value: Any, *, row_index: int, column_index: int) -> Any:
    """Normalize one BSON cell into the protected public scalar set.

    ``datetime`` is the only explicitly supported native representation in
    the first profile and is normalized to its ISO-8601 string form;
    unsupported values fail safely without exposing the raw value.
    """
    if isinstance(value, _SCALAR_TYPES):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    raise MongoExecutionError(
        "mongodb returned a value outside the protected scalar set",
        details={
            "row_index": str(row_index),
            "column_index": str(column_index),
            "value_type": type(value).__name__,
        },
    )


def _path_value(document: dict[str, Any], path: str) -> Any:
    value: Any = document
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _result_columns(
    spec: MongoQuerySpec, documents: tuple[dict[str, Any], ...]
) -> tuple[str, ...]:
    """Column names derived from the spec; falls back to first-doc keys."""
    if spec.operation == MongoOperation.COUNT:
        return ("count",)
    def _is_output(marker: Any) -> bool:
        return (
            marker == 1
            or (isinstance(marker, str) and marker.startswith("$"))
            or isinstance(marker, (Mapping, list, tuple))
        )

    if spec.operation == MongoOperation.FIND:
        if spec.projection:
            return tuple(
                path
                for path, marker in spec.projection.items()
                if _is_output(marker)
            )
    elif spec.pipeline is not None:
        last = spec.pipeline[-1]
        name, argument = next(iter(last.items()))
        if name == "$project":
            return tuple(
                path
                for path, marker in argument.items()
                if _is_output(marker)
            )
        if name == "$count":
            return (str(argument),)
        if name == "$group":
            return tuple(
                alias for alias in argument if alias != "_id"
            )
    if documents:
        return tuple(key for key in documents[0] if key != "_id")
    return ()


def execute_mongo_spec(
    executor: MongoExecutor,
    spec: MongoQuerySpec,
    *,
    max_rows: int = 100_000,
    max_columns: int = 1_000,
    max_result_bytes: int | None = None,
    timeout_seconds: float = 30.0,
) -> ExecutionResult:
    """Execute one validated spec and map to protected scalar rows."""
    started = time.monotonic()
    deadline = started + timeout_seconds

    documents = _execute_stage(executor, spec)

    if time.monotonic() >= deadline:
        raise MongoExecutionError(
            "mongodb execution exceeded the authorized timeout",
            details={"timeout_seconds": str(timeout_seconds)},
        )

    columns = _result_columns(spec, documents)
    if len(columns) > max_columns:
        raise MongoExecutionError(
            "result column count exceeds the bounded maximum",
            details={"max_columns": str(max_columns)},
        )

    rows: list[tuple[Any, ...]] = []
    for row_index, document in enumerate(documents):
        if row_index >= max_rows:
            raise MongoExecutionError(
                "result document count exceeds the bounded maximum",
                details={"max_rows": str(max_rows)},
            )
        row = tuple(
            normalize_bson_cell(
                _path_value(document, column),
                row_index=row_index,
                column_index=column_index,
            )
            for column_index, column in enumerate(columns)
        )
        rows.append(row)
        if max_result_bytes is not None:
            result_bytes = sum(len(str(column).encode("utf-8")) for column in columns) + sum(
                len(str(cell).encode("utf-8"))
                for current_row in rows
                for cell in current_row
            )
            if result_bytes > max_result_bytes:
                raise MongoExecutionError(
                    "result bytes exceed the authorized maximum",
                    details={
                        "max_result_bytes": str(max_result_bytes),
                        "actual": str(result_bytes),
                    },
                )
    if time.monotonic() >= deadline:
        raise MongoExecutionError(
            "mongodb execution exceeded the authorized timeout",
            details={"timeout_seconds": str(timeout_seconds)},
        )

    # Rows are model-native tuples; the fingerprint payload carries them
    # as JSON arrays so strict canonicalization stays fail-closed.
    fingerprint = strict_sha256_fingerprint(
        {
            "columns": list(columns),
            "rows": [list(row) for row in rows],
        }
    )
    duration_ms = int((time.monotonic() - started) * 1000)
    return ExecutionResult(
        result_id=f"result-{fingerprint[-16:]}",
        fingerprint=fingerprint,
        row_count=len(rows),
        columns=columns,
        rows=tuple(rows),
        duration_ms=duration_ms,
        metadata={"operation": spec.operation.value, "collection": spec.collection},
    )
