"""Read-only SQL execution with protected scalar result mapping.

Execution opens a read-only connection, bounds row handling, classifies
failures safely, and normalizes every value into the protected public
scalar set.  Unsupported native values become structured safe failures -
the native value itself never crosses the boundary.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from nl2data.errors import ErrorCategory, ErrorCode, NL2DataError
from nl2data_core.adapters.models import ExecutionResult
from nl2data_core.canonical import sha256_fingerprint

#: The protected public scalar set; everything else is unsupported.
_SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool, type(None))


class SQLExecutionError(NL2DataError):
    """Raised when SQL execution fails or produces unsupported values."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            ErrorCategory.ADAPTER,
            ErrorCode.SQL_EXECUTION_FAILED,
            message,
            retryable=False,
            details=details,
        )


def _normalize_cell(value: Any, *, row_index: int, column_index: int) -> Any:
    if isinstance(value, _SCALAR_TYPES):
        return value
    raise SQLExecutionError(
        "database returned a value outside the protected scalar set",
        details={
            "row_index": str(row_index),
            "column_index": str(column_index),
            "value_type": type(value).__name__,
        },
    )


def execute_sql(
    sql: str,
    *,
    db_path: Path,
    dialect: str = "sqlite",
    max_rows: int = 100_000,
    max_columns: int = 1_000,
    timeout_seconds: float = 30.0,
    max_result_bytes: int | None = None,
) -> ExecutionResult:
    """Execute one validated read-only statement and map to protected scalars."""
    if dialect != "sqlite":
        raise SQLExecutionError(
            f"execution is not implemented for dialect '{dialect}'",
            details={"dialect": dialect},
        )
    started = time.monotonic()
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=timeout_seconds)
    except sqlite3.Error as error:
        raise SQLExecutionError(
            "could not open the read-only database connection",
            details={"cause_type": type(error).__name__},
        ) from error

    try:
        with connection:
            connection.execute("PRAGMA query_only = ON")
            deadline = time.monotonic() + timeout_seconds
            connection.set_progress_handler(
                lambda: 1 if time.monotonic() >= deadline else 0,
                1_000,
            )
            cursor = connection.execute(sql)
            columns = tuple(description[0] for description in cursor.description or ())
            if len(columns) > max_columns:
                raise SQLExecutionError(
                    "result column count exceeds the bounded maximum",
                    details={"max_columns": str(max_columns)},
                )
            rows: list[tuple[Any, ...]] = []
            for row_index, row in enumerate(cursor.fetchmany(max_rows + 1)):
                if row_index >= max_rows:
                    raise SQLExecutionError(
                        "result row count exceeds the bounded maximum",
                        details={"max_rows": str(max_rows)},
                    )
                rows.append(
                    tuple(
                        _normalize_cell(cell, row_index=row_index, column_index=col_index)
                        for col_index, cell in enumerate(row)
                    )
                )
                if max_result_bytes is not None:
                    result_bytes = sum(
                        len(str(column).encode("utf-8")) for column in columns
                    ) + sum(
                        len(str(cell).encode("utf-8"))
                        for current_row in rows
                        for cell in current_row
                    )
                    if result_bytes > max_result_bytes:
                        raise SQLExecutionError(
                            "result bytes exceed the authorized maximum",
                            details={
                                "max_result_bytes": str(max_result_bytes),
                                "actual": str(result_bytes),
                            },
                        )
            if time.monotonic() >= deadline:
                raise SQLExecutionError(
                    "SQL execution exceeded the authorized timeout",
                    details={"timeout_seconds": str(timeout_seconds)},
                )
    except SQLExecutionError:
        raise
    except sqlite3.Error as error:
        raise SQLExecutionError(
            "SQL execution failed",
            details={"cause_type": type(error).__name__},
        ) from error
    finally:
        connection.close()

    fingerprint = sha256_fingerprint({"columns": columns, "rows": rows})
    duration_ms = int((time.monotonic() - started) * 1000)
    return ExecutionResult(
        result_id=f"result-{fingerprint[-16:]}",
        fingerprint=fingerprint,
        row_count=len(rows),
        columns=columns,
        rows=tuple(rows),
        duration_ms=duration_ms,
        metadata={"dialect": dialect},
    )
