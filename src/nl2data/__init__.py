"""NL2Data public package (distribution: ``nl2data-core``).

Only the symbols listed in ``__all__`` are part of the public API.
Application code must never import ``nl2data_core`` internals.
"""

from __future__ import annotations

from .engine import LifecycleError, NL2DataEngine
from .errors import (
    ErrorCategory,
    ErrorCode,
    ErrorRecord,
    NL2DataError,
    as_error_record,
)
from .models import (
    EngineCapabilitySnapshot,
    EngineHealth,
    HealthStatus,
    LifecycleState,
    OutcomeStatus,
    QueryClarification,
    QueryClarificationOption,
    QueryContext,
    QueryOptions,
    QueryOutcome,
    QueryRequest,
    QueryResult,
)

__all__ = [
    "EngineCapabilitySnapshot",
    "EngineHealth",
    "ErrorCategory",
    "ErrorCode",
    "ErrorRecord",
    "HealthStatus",
    "LifecycleError",
    "LifecycleState",
    "NL2DataEngine",
    "NL2DataError",
    "OutcomeStatus",
    "QueryContext",
    "QueryClarification",
    "QueryClarificationOption",
    "QueryOptions",
    "QueryOutcome",
    "QueryRequest",
    "QueryResult",
    "as_error_record",
]
