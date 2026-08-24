"""Lazy MongoDB client lifecycle with bounded readiness checks.

The handle wraps any :class:`MongoExecutor` and centralizes the lifecycle
contract: lazy connection, connection/readiness checks, idempotent close,
and safe unavailable-driver/service errors.  The adapter never touches a
native client directly.
"""

from __future__ import annotations

from typing import Any

from .executor import MongoExecutor
from .models import MongoUnavailableError


class MongoClientHandle:
    """Lifecycle wrapper around one executor; connect is lazy and idempotent.

    ``available()`` performs the bounded readiness check (driver import and
    service ping for the PyMongo profile; always true for the fake profile).
    ``ensure_ready()`` raises a safe :class:`MongoUnavailableError` instead
    of a native driver exception, and ``close()`` is idempotent.
    """

    def __init__(self, executor: MongoExecutor) -> None:
        self._executor = executor
        self._closed = False

    @property
    def executor(self) -> MongoExecutor:
        """The bound executor (adapter-internal boundary only)."""
        return self._executor

    @property
    def closed(self) -> bool:
        return self._closed

    def available(self) -> bool:
        """Whether the driver/service is ready; never raises for absence."""
        if self._closed:
            return False
        try:
            return self._executor.available()
        except Exception:
            return False

    def ensure_ready(self) -> None:
        """Raise :class:`MongoUnavailableError` when the backend is not ready."""
        if self._closed:
            raise MongoUnavailableError(
                "the mongodb client is closed",
                details={"cause_type": "ClosedClient"},
            )
        try:
            ready = self._executor.available()
        except Exception:
            ready = False
        if not ready:
            raise MongoUnavailableError(
                "the mongodb driver or service is unavailable",
                details={"cause_type": "Unavailable"},
            )

    def close(self) -> None:
        """Release the backend; safe to call more than once."""
        if self._closed:
            return
        self._executor.close()
        self._closed = True

    # -- executor delegation (validated specs only) -------------------------

    def find_documents(self, **kwargs: Any) -> Any:
        self.ensure_ready()
        return self._executor.find_documents(**kwargs)

    def aggregate_documents(self, **kwargs: Any) -> Any:
        self.ensure_ready()
        return self._executor.aggregate_documents(**kwargs)

    def count_documents(self, **kwargs: Any) -> int:
        self.ensure_ready()
        return self._executor.count_documents(**kwargs)

    def list_collections(self) -> tuple[str, ...]:
        self.ensure_ready()
        return self._executor.list_collections()

    def sample_document(self, collection: str) -> dict[str, Any] | None:
        self.ensure_ready()
        return self._executor.sample_document(collection)
