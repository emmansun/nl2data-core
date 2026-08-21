"""The single canonical async-first QueryAdapter contract (DDS-002)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import (
    AdapterCapabilities,
    CostEstimate,
    ExecutionResult,
    GeneratedArtifact,
    ParsedArtifact,
    ValidatedArtifact,
    ValidationContext,
)


@runtime_checkable
class QueryAdapter(Protocol):
    """One generic async-first adapter contract.

    Synchronous methods (``capabilities``, ``parse``, ``validate``) are pure
    and side-effect free; all I/O boundaries (``generate``, ``estimate_cost``,
    ``execute``, ``close``) are asynchronous.  No SQL-, MongoDB- or
    LLM-specific method exists in the core contract.
    """

    def capabilities(self) -> AdapterCapabilities:
        """Declare adapter capabilities, including the async mode."""
        ...

    def parse(self, query: str, context: ValidationContext) -> ParsedArtifact:
        """Parse a query into a generic artifact without side effects."""
        ...

    def validate(self, artifact: ParsedArtifact, context: ValidationContext) -> ValidatedArtifact:
        """Validate a parsed artifact without side effects."""
        ...

    async def generate(self, query: str, context: ValidationContext) -> GeneratedArtifact:
        """Generate an executable artifact (I/O boundary)."""
        ...

    async def estimate_cost(
        self, artifact: ValidatedArtifact, context: ValidationContext
    ) -> CostEstimate:
        """Estimate execution cost (I/O boundary)."""
        ...

    async def execute(
        self, artifact: ValidatedArtifact, context: ValidationContext
    ) -> ExecutionResult:
        """Execute a validated artifact (I/O boundary)."""
        ...

    async def close(self) -> None:
        """Release adapter resources (I/O boundary)."""
        ...
