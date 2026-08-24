"""NL2Data public package (distribution: ``nl2data-core``).

Only the symbols listed in ``__all__`` are part of the public API.
Application code must never import ``nl2data_core`` internals; the public
facade composes them through :class:`nl2data.composition.CompositionProfile`.
"""

from __future__ import annotations

from nl2data_core.config.loader import load_config

from .composition import (
    CompositionProfile,
    MemoryProviderPort,
    ModelProviderPort,
    QueryAdapterPort,
    TelemetryPort,
    WorkflowRuntimePort,
)
from .engine import LifecycleError, NL2DataEngine
from .errors import (
    ErrorCategory,
    ErrorCode,
    ErrorRecord,
    NL2DataError,
    SyncUsageError,
    as_error_record,
)
from .facade import FacadePort, NL2Data, create_facade
from .models import (
    CancellationRequest,
    CancellationResult,
    CancellationStatus,
    EngineCapabilitySnapshot,
    EngineHealth,
    FacadeCapabilities,
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
    WorkflowEvent,
    WorkflowHandle,
    WorkflowStage,
    WorkflowStatus,
)

__all__ = [
    "CancellationRequest",
    "CancellationResult",
    "CancellationStatus",
    "CompositionProfile",
    "EngineCapabilitySnapshot",
    "EngineHealth",
    "ErrorCategory",
    "ErrorCode",
    "ErrorRecord",
    "FacadeCapabilities",
    "FacadePort",
    "HealthStatus",
    "LifecycleError",
    "LifecycleState",
    "MemoryProviderPort",
    "ModelProviderPort",
    "NL2Data",
    "NL2DataEngine",
    "NL2DataError",
    "OutcomeStatus",
    "QueryAdapterPort",
    "QueryClarification",
    "QueryClarificationOption",
    "QueryContext",
    "QueryOptions",
    "QueryOutcome",
    "QueryRequest",
    "QueryResult",
    "SyncUsageError",
    "TelemetryPort",
    "WorkflowEvent",
    "WorkflowHandle",
    "WorkflowRuntimePort",
    "WorkflowStage",
    "WorkflowStatus",
    "as_error_record",
    "create_facade",
    "load_config",
]
