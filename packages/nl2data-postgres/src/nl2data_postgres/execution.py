"""Read-only PostgreSQL SQL execution with bounded protected results."""

from __future__ import annotations

import time
from typing import Any

from nl2data_core.adapters.models import ExecutionResult
from nl2data_core.canonical import sha256_fingerprint

from .client import PostgresPool
from .config import PostgresAdapterConfig
from .errors import PostgresExecutionError

#: The protected public scalar set; everything else is unsupported.
_SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool, type(None))


def _normalize_cell(value: Any, *, row_index: int, column_index: int) -> Any:
    if isinstance(value, _SCALAR_TYPES):
        return value
    raise PostgresExecutionError(
        "database returned a value outside the protected scalar set",
        details={
            "row_index": str(row_index),
            "column_index": str(column_index),
            "value_type": type(value).__name__,
        },
    )


class PostgresExecutor:
    """Bounded read-only executor for a single PostgreSQL adapter."""

    def __init__(self, config: PostgresAdapterConfig) -> None:
        self._config = config
        self._pool = PostgresPool(config)

    def close(self) -> None:
        self._pool.close()

    def execute(
        self,
        sql: str,
        *,
        max_rows: int | None = None,
        max_columns: int | None = None,
        max_result_bytes: int | None = None,
        timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        """Execute one validated read-only statement and map to protected scalars."""
        effective_max_rows = max_rows if max_rows is not None else self._config.max_rows
        effective_max_columns = max_columns if max_columns is not None else 1_000
        effective_max_bytes = (
            max_result_bytes if max_result_bytes is not None else self._config.max_result_bytes
        )
        effective_timeout = timeout_seconds or self._config.timeout_seconds
        started = time.monotonic()
        deadline = started + effective_timeout

        with self._pool.connection() as connection:
            timeout_ms = max(1, int(effective_timeout * 1000))
            connection.execute(f"SET statement_timeout = {timeout_ms}")
            try:
                cursor = connection.execute(sql)
            except Exception as error:
                raise PostgresExecutionError(
                    "postgresql execution failed",
                    details={"cause_type": type(error).__name__},
                ) from error

            columns = tuple(description[0] for description in cursor.description or ())
            if len(columns) > effective_max_columns:
                raise PostgresExecutionError(
                    "result column count exceeds the bounded maximum",
                    details={"max_columns": str(effective_max_columns)},
                )

            rows: list[tuple[Any, ...]] = []
            for row_index, row in enumerate(cursor.fetchmany(effective_max_rows + 1)):
                if row_index >= effective_max_rows:
                    raise PostgresExecutionError(
                        "result row count exceeds the bounded maximum",
                        details={"max_rows": str(effective_max_rows)},
                    )
                rows.append(
                    tuple(
                        _normalize_cell(cell, row_index=row_index, column_index=col_index)
                        for col_index, cell in enumerate(row)
                    )
                )
                if effective_max_bytes is not None:
                    text_bytes = sum(
                        len(str(cell).encode("utf-8"))
                        for current_row in rows
                        for cell in current_row
                    )
                    result_bytes = sum(len(str(column).encode("utf-8")) for column in columns)
                    result_bytes += text_bytes
                    if result_bytes > effective_max_bytes:
                        raise PostgresExecutionError(
                            "result bytes exceed the authorized maximum",
                            details={
                                "max_result_bytes": str(effective_max_bytes),
                                "actual": str(result_bytes),
                            },
                        )
            if time.monotonic() >= deadline:
                raise PostgresExecutionError(
                    "SQL execution exceeded the authorized timeout",
                    details={"timeout_seconds": str(effective_timeout)},
                )

        fingerprint = sha256_fingerprint({"columns": columns, "rows": rows})
        duration_ms = int((time.monotonic() - started) * 1000)
        return ExecutionResult(
            result_id=f"result-{fingerprint[-16:]}",
            fingerprint=fingerprint,
            row_count=len(rows),
            columns=columns,
            rows=tuple(rows),
            duration_ms=duration_ms,
            metadata={"dialect": "postgres"},
        )
