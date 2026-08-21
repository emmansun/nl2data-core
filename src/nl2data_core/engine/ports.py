"""Internal workflow execution port used by the engine.

The engine routes public query requests exclusively through this port and
never invokes native database, LLM, or provider executors directly.
"""

from __future__ import annotations

from typing import Protocol

from nl2data.errors import ErrorCategory, ErrorCode, ErrorRecord
from nl2data.models import OutcomeStatus, QueryOutcome, QueryRequest

NOT_CONFIGURED_MESSAGE = "no executable workflow is configured for this engine"


class WorkflowExecutionPort(Protocol):
    """The narrow execution boundary consumed by the public engine."""

    def is_configured(self) -> bool:
        """Whether an executable workflow is available."""
        ...

    async def execute(self, request: QueryRequest) -> QueryOutcome:
        """Execute a public query request and return a protected outcome."""
        ...

    async def close(self) -> None:
        """Release workflow resources (idempotent)."""
        ...


class NotConfiguredWorkflowRunner:
    """P0 implementation that never fabricates results.

    Returns a stable not-configured outcome instead of pretending to
    execute a workflow.
    """

    def is_configured(self) -> bool:
        return False

    async def execute(self, request: QueryRequest) -> QueryOutcome:
        return QueryOutcome(
            status=OutcomeStatus.NOT_CONFIGURED,
            request_id=request.request_id,
            error=ErrorRecord(
                code=ErrorCode.NOT_CONFIGURED,
                category=ErrorCategory.NOT_CONFIGURED,
                message=NOT_CONFIGURED_MESSAGE,
                retryable=False,
            ),
        )

    async def close(self) -> None:
        return None
