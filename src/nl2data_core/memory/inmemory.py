"""Deterministic in-memory memory provider.

Records are stored in insertion order and recalled in deterministic
``(created_at, record_id)`` order.  Scope matching is fail-closed: records
bound to a tenant scope never surface without the exact fingerprint, and
records from another session or conversation are never exposed.  All
bounds (TTL, capacity, recall budget) are enforced structurally so a
projection can never exceed a limit.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from nl2data_core.canonical import canonical_json
from nl2data_core.memory.errors import (
    MemoryErrorCode,
    MemoryInvocationError,
)
from nl2data_core.memory.models import (
    MemoryRecallBudget,
    MemoryRecallProjection,
    MemoryRecord,
    MemoryScope,
)

#: Approximate token size used to enforce the recall token budget.
_MAX_CHARS_PER_TOKEN = 4


def _utc_now() -> datetime:
    return datetime.now(UTC)


class InMemoryMemoryProvider:
    """Deterministic single-process memory provider.

    ``available=False`` makes every operation raise a normalized
    ``MEMORY_UNAVAILABLE`` error so callers can degrade statelessly.
    ``clock`` is injectable for deterministic expiry/compaction tests.
    """

    def __init__(
        self,
        *,
        available: bool = True,
        max_records: int = 10_000,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._available = available
        self._max_records = max_records
        self._clock = clock or _utc_now
        self._records: dict[str, MemoryRecord] = {}
        self._order: list[str] = []
        self._expired_ids: set[str] = set()

    def is_available(self) -> bool:
        """Whether the provider can serve requests right now."""
        return self._available

    def _ensure_available(self) -> None:
        if not self._available:
            raise MemoryInvocationError(
                MemoryErrorCode.MEMORY_UNAVAILABLE,
                "memory provider is unavailable",
            )

    def append(self, record: MemoryRecord) -> str:
        """Store ``record`` and return its stable record id."""
        self._ensure_available()
        if record.record_id in self._records or record.record_id in self._expired_ids:
            raise MemoryInvocationError(
                MemoryErrorCode.RECORD_REJECTED,
                "memory record id already exists",
                details={"record_id": record.record_id},
            )
        if len(self._records) >= self._max_records:
            raise MemoryInvocationError(
                MemoryErrorCode.BUDGET_EXCEEDED,
                "memory provider capacity is exhausted",
                details={"max_records": str(self._max_records)},
            )
        self._records[record.record_id] = record
        self._order.append(record.record_id)
        return record.record_id

    def recall(
        self,
        *,
        scope: MemoryScope,
        budget: MemoryRecallBudget | None = None,
        now: datetime | None = None,
    ) -> MemoryRecallProjection:
        """Return the bounded projection of fresh matching records."""
        self._ensure_available()
        now = now or self._clock()
        recall_budget = budget or MemoryRecallBudget()
        eligible: list[MemoryRecord] = []
        for record_id in self._order:
            record = self._records.get(record_id)
            if record is None or record.is_expired(now=now):
                continue
            if not self._scope_matches(record.scope, scope):
                continue
            eligible.append(record)
        eligible.sort(key=lambda record: (record.created_at, record.record_id))
        selected: list[MemoryRecord] = []
        char_count = 0
        truncated = False
        for record in eligible:
            size = self._record_size(record)
            token_estimate = (char_count + size) // _MAX_CHARS_PER_TOKEN
            if (
                len(selected) >= recall_budget.max_records
                or char_count + size > recall_budget.max_chars
                or token_estimate > recall_budget.max_tokens
            ):
                truncated = True
                break
            selected.append(record)
            char_count += size
        return MemoryRecallProjection(
            scope_fingerprint=scope.fingerprint,
            records=tuple(selected),
            truncated=truncated,
            char_count=char_count,
            token_estimate=char_count // _MAX_CHARS_PER_TOKEN,
        )

    def compare_and_set(
        self,
        *,
        expected: MemoryRecord,
        replacement: MemoryRecord,
    ) -> bool:
        """Optimistically replace ``expected`` with ``replacement``."""
        self._ensure_available()
        if replacement.record_id != expected.record_id:
            raise MemoryInvocationError(
                MemoryErrorCode.RECORD_REJECTED,
                "replacement record id must match the expected record",
                details={
                    "expected": expected.record_id,
                    "replacement": replacement.record_id,
                },
            )
        if replacement.scope.fingerprint != expected.scope.fingerprint:
            raise MemoryInvocationError(
                MemoryErrorCode.SCOPE_MISMATCH,
                "replacement record scope must match the expected record",
            )
        stored = self._records.get(expected.record_id)
        if stored is None or stored.fingerprint != expected.fingerprint:
            return False
        self._records[expected.record_id] = replacement
        return True

    def compact(self, *, now: datetime | None = None) -> int:
        """Drop expired records and return the number removed."""
        self._ensure_available()
        now = now or self._clock()
        removed = 0
        remaining: list[str] = []
        for record_id in self._order:
            record = self._records.get(record_id)
            if record is None:
                self._expired_ids.discard(record_id)
                continue
            if record.is_expired(now=now):
                self._records.pop(record_id, None)
                removed += 1
                continue
            remaining.append(record_id)
        self._order = remaining
        return removed

    def expire(self, record_id: str) -> bool:
        """Expire one record; its id stays reserved for the provider lifetime."""
        self._ensure_available()
        if record_id not in self._records:
            return False
        self._records.pop(record_id, None)
        self._expired_ids.add(record_id)
        return True

    def delete(self, record_id: str) -> bool:
        """Delete one record; its id may be reused afterwards."""
        self._ensure_available()
        if record_id not in self._records:
            return False
        self._records.pop(record_id, None)
        return True

    @staticmethod
    def _scope_matches(record_scope: MemoryScope, query_scope: MemoryScope) -> bool:
        """Fail-closed scope match: never expose what was not asked for."""
        if query_scope.tenant_scope_fingerprint is not None:
            if record_scope.tenant_scope_fingerprint != query_scope.tenant_scope_fingerprint:
                return False
        elif record_scope.tenant_scope_fingerprint is not None:
            return False
        if record_scope.session_id != query_scope.session_id:
            return False
        if query_scope.conversation_id is not None and (
            record_scope.conversation_id != query_scope.conversation_id
        ):
            return False
        adapter_matches = query_scope.adapter_id is None or (
            record_scope.adapter_id == query_scope.adapter_id
        )
        source_matches = query_scope.source_id is None or (
            record_scope.source_id == query_scope.source_id
        )
        return adapter_matches and source_matches

    @staticmethod
    def _record_size(record: MemoryRecord) -> int:
        """Canonical serialized size used for character-budget accounting."""
        return len(canonical_json(record.safe_dump()))
