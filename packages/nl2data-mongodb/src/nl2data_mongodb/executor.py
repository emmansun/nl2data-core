"""Driver-neutral MongoDB executor port.

The adapter validates typed specs and hands only validated structures to
an executor; executors translate them into driver calls.  Raw documents
returned by an executor stay inside the execution boundary - the adapter
normalizes them into protected scalar rows before the public result.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MongoExecutor(Protocol):
    """One deterministic executor boundary behind the MongoDB adapter.

    All methods are synchronous; the async adapter offloads them to a
    worker thread.  ``find_documents``, ``aggregate_documents``, and
    ``count_documents`` receive only validated structured arguments.
    """

    def find_documents(
        self,
        *,
        collection: str,
        filter_: Mapping[str, Any],
        projection: Mapping[str, Any],
        sort: Mapping[str, int],
        skip: int | None,
        limit: int | None,
    ) -> tuple[dict[str, Any], ...]:
        """Run a validated find and return raw documents (executor-local)."""
        ...

    def aggregate_documents(
        self,
        *,
        collection: str,
        pipeline: tuple[Mapping[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        """Run a validated aggregation pipeline and return raw documents."""
        ...

    def count_documents(
        self,
        *,
        collection: str,
        filter_: Mapping[str, Any],
    ) -> int:
        """Count documents matching a validated filter."""
        ...

    def list_collections(self) -> tuple[str, ...]:
        """List bounded collection names for metadata discovery."""
        ...

    def sample_document(self, collection: str) -> dict[str, Any] | None:
        """Return one raw sample document (metadata discovery only)."""
        ...

    def available(self) -> bool:
        """Whether the driver/service is ready for execution."""
        ...

    def close(self) -> None:
        """Release resources; idempotent."""
        ...
