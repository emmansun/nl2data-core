"""The replaceable Memory provider port.

The provider stores immutable records, recalls bounded projections,
compares-and-sets optimistic updates, and manages expiry/deletion.  All
operations are synchronous and side-effect bounded; a provider reports
``is_available()`` so callers can degrade statelessly instead of failing
the query when memory is down.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .models import (
    MemoryRecallBudget,
    MemoryRecallProjection,
    MemoryRecord,
    MemoryScope,
)


@runtime_checkable
class MemoryProvider(Protocol):
    """One deterministic memory provider contract.

    Implementations must never expose records outside the requested scope
    and must never store or return raw payload material.  All methods are
    synchronous; durable providers remain future work.
    """

    def is_available(self) -> bool:
        """Whether the provider can serve requests right now."""
        ...

    def append(self, record: MemoryRecord) -> str:
        """Store ``record`` and return its stable record id.

        Raises :class:`MemoryInvocationError` (``RECORD_REJECTED``) for
        duplicates and ``BUDGET_EXCEEDED`` when the provider capacity is
        exhausted.
        """
        ...

    def recall(
        self,
        *,
        scope: MemoryScope,
        budget: MemoryRecallBudget | None = None,
        now: datetime | None = None,
    ) -> MemoryRecallProjection:
        """Return the bounded projection of fresh records for ``scope``.

        Scope matching is fail-closed: records bound to a tenant scope are
        never returned unless the query carries the exact fingerprint, and
        records from another conversation are never exposed.
        """
        ...

    def compare_and_set(
        self,
        *,
        expected: MemoryRecord,
        replacement: MemoryRecord,
    ) -> bool:
        """Optimistically replace ``expected`` with ``replacement``.

        Returns ``False`` when the expected fingerprint no longer matches
        the stored record; raises ``RECORD_REJECTED`` when the replacement
        id differs and ``SCOPE_MISMATCH`` when the scopes differ.
        """
        ...

    def compact(self, *, now: datetime | None = None) -> int:
        """Drop expired records and return the number removed."""
        ...

    def expire(self, record_id: str) -> bool:
        """Expire one record; returns whether it existed."""
        ...

    def delete(self, record_id: str) -> bool:
        """Delete one record; returns whether it existed."""
        ...
