"""Redis-backed shared memory provider implementing ``MemoryProvider``.

Records persist under deterministic namespaced keys so separate workers
and Pods sharing one Redis service observe the same bounded context:

- ``{namespace}:ids:{record_id}`` - global record-id registry; its
  create-if-absent write makes record-id uniqueness atomic across
  provider instances and it maps an id back to its record key for
  ``expire``/``delete``.
- ``{namespace}:record:{tenant}:{session}:{record_id}`` - the versioned
  serialization envelope (or a tombstone after explicit expiry).
- ``{namespace}:index:{tenant}:{session}`` - a best-effort set of record
  keys used for deterministic recall; recall and compaction tolerate
  stale members, and the index is bounded by the configured candidate
  and batch limits.

Tenant scope fingerprints (or an explicit ``global`` marker) and session
identifiers keep keys isolated; every recalled record is revalidated in
application code with the fail-closed scope matching rules.  Connection,
timeout, serialization, and backend failures are normalized into the
existing :class:`MemoryInvocationError` codes - never credentials, urls,
or raw backend details - so the runtime keeps its stateless fallback.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from nl2data._redact import REDACTED_VALUE
from nl2data_core.canonical import canonical_json
from nl2data_core.memory.errors import MemoryErrorCode, MemoryInvocationError
from nl2data_core.memory.models import (
    MemoryRecallBudget,
    MemoryRecallProjection,
    MemoryRecord,
    MemoryScope,
)

from .client import build_redis_client, driver_available, is_redis_error, is_watch_error
from .config import RedisMemoryConfig
from .serialization import deserialize_record_value, serialize_record, serialize_tombstone

#: Approximate token size used to enforce the recall token budget.
_MAX_CHARS_PER_TOKEN = 4

#: Key component used when a record is not bound to a tenant scope.
_GLOBAL_TENANT_MARKER = "global"

#: Bounded identifier accepted for ``expire``/``delete`` record ids.
_RECORD_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$")


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RedisMemoryProvider:
    """Shared memory provider persisting safe records in Redis.

    ``config`` carries validated behavior bounds only; the connection is
    either an injected ``client`` (fake or host-managed pool - never
    closed by this provider) or a lazy client built from ``url`` with
    bounded socket timeouts.  ``clock`` is injectable for deterministic
    TTL/expiry tests.  An expired record id stays reserved for the
    configured retention window, after which it may be reused.
    """

    def __init__(
        self,
        config: RedisMemoryConfig,
        *,
        url: str | None = None,
        client: Any | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if url is None and client is None:
            raise MemoryInvocationError(
                MemoryErrorCode.MEMORY_UNAVAILABLE,
                "redis memory provider requires a url or an injected client",
                details={"cause_type": "ConfigurationError"},
            )
        if url is not None and not driver_available():
            raise MemoryInvocationError(
                MemoryErrorCode.MEMORY_UNAVAILABLE,
                "the redis driver is not installed; install the 'redis' extra",
                details={"cause_type": "ImportError"},
            )
        self._config = config
        self._url = url
        self._injected = client
        self._client: Any = None
        self._clock = clock or _utc_now
        self._closed = False

    # -- provider lifecycle -------------------------------------------------

    def is_available(self) -> bool:
        """Whether the backend can serve requests right now (bounded ping)."""
        if self._closed:
            return False
        try:
            self._client_connection().ping()
            return True
        except Exception:
            return False

    def close(self) -> None:
        """Release the self-created client; injected clients stay host-owned."""
        self._closed = True
        if self._client is not None:
            with suppress(Exception):
                self._client.close()
            self._client = None

    def _client_connection(self) -> Any:
        if self._closed:
            raise MemoryInvocationError(
                MemoryErrorCode.MEMORY_UNAVAILABLE, "memory provider is closed"
            )
        if self._injected is not None:
            return self._injected
        if self._client is not None:
            return self._client
        self._client = build_redis_client(
            self._url or "",
            connect_timeout_seconds=self._config.connect_timeout_seconds,
            command_timeout_seconds=self._config.command_timeout_seconds,
        )
        return self._client

    # -- keys ---------------------------------------------------------------

    @staticmethod
    def _tenant_component(scope: MemoryScope) -> str:
        """Tenant key component: the fingerprint or an explicit global marker."""
        return scope.tenant_scope_fingerprint or _GLOBAL_TENANT_MARKER

    def _id_key(self, record_id: str) -> str:
        return f"{self._config.namespace}:ids:{record_id}"

    def _record_key(self, scope: MemoryScope, record_id: str) -> str:
        tenant = self._tenant_component(scope)
        return (
            f"{self._config.namespace}:record:{tenant}:{scope.session_id}:{record_id}"
        )

    def _index_key(self, scope: MemoryScope) -> str:
        tenant = self._tenant_component(scope)
        return f"{self._config.namespace}:index:{tenant}:{scope.session_id}"

    @staticmethod
    def _validate_record_id(record_id: str) -> None:
        """Reject ids that could corrupt a namespaced key component."""
        if not isinstance(record_id, str) or not _RECORD_ID_PATTERN.fullmatch(record_id):
            raise MemoryInvocationError(
                MemoryErrorCode.RECORD_REJECTED,
                "record id is not a bounded identifier",
                details={"cause_type": "identifier"},
            )

    # -- mutations ----------------------------------------------------------

    def append(self, record: MemoryRecord) -> str:
        """Store ``record`` atomically and return its stable record id.

        Record-id uniqueness is enforced by a create-if-absent write on
        the id registry, so concurrent provider instances can never
        create two records under the same id.  Capacity is enforced
        against the session index (best-effort atomic); a record whose
        TTL exceeds the configured bound is rejected.
        """
        client = self._client_connection()
        if record.ttl_seconds > self._config.max_ttl_seconds:
            raise MemoryInvocationError(
                MemoryErrorCode.RECORD_REJECTED,
                "record ttl exceeds the provider bound",
                details={"max_ttl_seconds": str(self._config.max_ttl_seconds)},
            )
        id_key = self._id_key(record.record_id)
        record_key = self._record_key(record.scope, record.record_id)
        index_key = self._index_key(record.scope)
        try:
            ttl_seconds = self._remaining_ttl(record)
            for _ in range(8):
                with client.pipeline() as pipe:
                    pipe.watch(id_key, record_key, index_key)
                    if pipe.get(id_key) is not None or pipe.get(record_key) is not None:
                        raise MemoryInvocationError(
                            MemoryErrorCode.RECORD_REJECTED,
                            "memory record id already exists",
                            details={"record_id": record.record_id},
                        )
                    if pipe.scard(index_key) >= self._config.max_records:
                        raise MemoryInvocationError(
                            MemoryErrorCode.BUDGET_EXCEEDED,
                            "memory provider capacity is exhausted",
                            details={"max_records": str(self._config.max_records)},
                        )
                    pipe.multi()
                    pipe.set(id_key, record_key, ex=ttl_seconds)
                    pipe.set(record_key, serialize_record(record), ex=ttl_seconds)
                    pipe.sadd(index_key, record_key)
                    try:
                        pipe.execute()
                        return record.record_id
                    except Exception as error:
                        if is_watch_error(error):
                            continue
                        raise
            raise MemoryInvocationError(
                MemoryErrorCode.RECORD_REJECTED,
                "memory record append conflicted with concurrent updates",
                details={"record_id": record.record_id},
            )
        except MemoryInvocationError:
            raise
        except Exception as error:
            raise self._normalize_error(error, operation="append") from error

    def compare_and_set(
        self,
        *,
        expected: MemoryRecord,
        replacement: MemoryRecord,
    ) -> bool:
        """Optimistically replace ``expected`` with ``replacement``.

        The stored fingerprint and scope are checked inside a watched
        transaction, so a concurrent replacement can never be overwritten
        by a stale attempt: every stale attempt returns ``False``.
        Replacement TTL is preserved from ``replacement.expires_at``.
        """
        client = self._client_connection()
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
        record_key = self._record_key(expected.scope, expected.record_id)
        try:
            with client.pipeline() as pipe:
                pipe.watch(record_key)
                raw = pipe.get(record_key)
                stored = None if raw is None else deserialize_record_value(raw)
                if (
                    stored is None
                    or stored.scope.fingerprint != expected.scope.fingerprint
                    or stored.fingerprint != expected.fingerprint
                ):
                    return False
                expires = replacement.expires_at or self._clock()
                remaining_ms = max(
                    1, int((expires - self._clock()).total_seconds() * 1000)
                )
                pipe.multi()
                pipe.set(record_key, serialize_record(replacement), px=remaining_ms)
                pipe.execute()
                return True
        except MemoryInvocationError:
            raise
        except Exception as error:
            if is_watch_error(error):
                # A watched key changed: the expected fingerprint is stale.
                return False
            raise self._normalize_error(error, operation="compare_and_set") from error

    def expire(self, record_id: str) -> bool:
        """Expire one record; its id stays reserved for the retention window."""
        client = self._client_connection()
        self._validate_record_id(record_id)
        try:
            id_key = self._id_key(record_id)
            for _ in range(8):
                with client.pipeline() as pipe:
                    pipe.watch(id_key)
                    record_key = pipe.get(id_key)
                    if record_key is None:
                        return False
                    record_key = self._as_text(record_key)
                    pipe.watch(record_key)
                    if pipe.get(record_key) is None:
                        return False
                    pipe.multi()
                    pipe.set(
                        record_key,
                        serialize_tombstone(),
                        ex=self._config.expired_id_retention_seconds,
                    )
                    pipe.expire(id_key, self._config.expired_id_retention_seconds)
                    try:
                        pipe.execute()
                        return True
                    except Exception as error:
                        if is_watch_error(error):
                            continue
                        raise
            return False
        except MemoryInvocationError:
            raise
        except Exception as error:
            raise self._normalize_error(error, operation="expire") from error

    def delete(self, record_id: str) -> bool:
        """Delete one record; its id may be reused immediately afterwards."""
        client = self._client_connection()
        self._validate_record_id(record_id)
        try:
            id_key = self._id_key(record_id)
            for _ in range(8):
                with client.pipeline() as pipe:
                    pipe.watch(id_key)
                    record_key = pipe.get(id_key)
                    if record_key is None:
                        return False
                    record_key = self._as_text(record_key)
                    pipe.watch(record_key)
                    pipe.multi()
                    pipe.delete(record_key)
                    pipe.delete(id_key)
                    try:
                        pipe.execute()
                        return True
                    except Exception as error:
                        if is_watch_error(error):
                            continue
                        raise
            return False
        except MemoryInvocationError:
            raise
        except Exception as error:
            raise self._normalize_error(error, operation="delete") from error

    def compact(self, *, now: datetime | None = None) -> int:
        """Drop expired/dead index entries and return the number removed.

        Scans only the configured namespace's index keys in bounded
        batches.  Records that died by TTL, explicit expiry (tombstones),
        or time-based expiry are removed from their index (and expired
        storage is deleted); malformed stored values are treated as stale
        and removed rather than failing the whole pass.
        """
        client = self._client_connection()
        now = now or self._clock()
        removed = 0
        try:
            for index_key in client.scan_iter(
                match=f"{self._config.namespace}:index:*",
                count=self._config.compaction_batch_size,
            ):
                index_key = self._as_text(index_key)
                for member in client.sscan_iter(
                    index_key, count=self._config.compaction_batch_size
                ):
                    member = self._as_text(member)
                    raw = client.get(member)
                    if raw is None:
                        client.srem(index_key, member)
                        removed += 1
                        continue
                    try:
                        record = deserialize_record_value(raw)
                    except MemoryInvocationError as error:
                        if error.code is not MemoryErrorCode.RECORD_REJECTED:
                            raise
                        client.srem(index_key, member)
                        client.delete(member)
                        removed += 1
                        continue
                    if record is None or record.is_expired(now=now):
                        client.srem(index_key, member)
                        if record is not None:
                            client.delete(member)
                        removed += 1
            return removed
        except MemoryInvocationError:
            raise
        except Exception as error:
            raise self._normalize_error(error, operation="compact") from error

    # -- recall -------------------------------------------------------------

    def recall(
        self,
        *,
        scope: MemoryScope,
        budget: MemoryRecallBudget | None = None,
        now: datetime | None = None,
    ) -> MemoryRecallProjection:
        """Return the bounded projection of fresh records for ``scope``.

        Candidate ids are read from the scoped index in bounded batches,
        each stored value is fully revalidated, and fail-closed scope
        matching runs in application code so a stale index member can
        never authorize a record.  Results are deterministic
        ``(created_at, record_id)`` order and honor the same
        record/character/token budgets as the in-memory provider.
        """
        client = self._client_connection()
        now = now or self._clock()
        recall_budget = budget or MemoryRecallBudget()
        index_key = self._index_key(scope)
        try:
            candidates: list[str] = []
            for member in client.sscan_iter(
                index_key, count=self._config.recall_batch_size
            ):
                candidates.append(self._as_text(member))
                if len(candidates) >= self._config.max_candidates:
                    break
            eligible: list[MemoryRecord] = []
            seen: set[str] = set()
            for record_key in candidates:
                if record_key in seen:
                    continue
                seen.add(record_key)
                raw = client.get(record_key)
                if raw is None:
                    continue
                record = deserialize_record_value(raw)
                if record is None:
                    continue
                if record.is_expired(now=now):
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
        except MemoryInvocationError:
            raise
        except Exception as error:
            raise self._normalize_error(error, operation="recall") from error

    # -- helpers ------------------------------------------------------------

    def _remaining_ttl(self, record: MemoryRecord) -> int:
        """Whole seconds until expiry; expired records die as fast as Redis allows."""
        expires = record.expires_at or self._clock()
        return max(1, int((expires - self._clock()).total_seconds()))

    @staticmethod
    def _safe_delete(client: Any, key: str) -> None:
        with suppress(Exception):
            client.delete(key)

    @staticmethod
    def _as_text(value: Any) -> str:
        """Decode driver bytes or materialize the injected string value."""
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

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

    @staticmethod
    def _normalize_error(error: BaseException, *, operation: str) -> MemoryInvocationError:
        """Map backend failures onto safe existing Memory error semantics.

        Connection, timeout, and driver errors become retryable
        ``MEMORY_UNAVAILABLE``; validation/serialization errors become
        ``RECORD_REJECTED``; anything else is redacted.  Credentials,
        urls, and raw exception text never cross this boundary.
        """
        if isinstance(error, MemoryInvocationError):
            return error
        if (
            isinstance(error, (TimeoutError, ConnectionError, OSError))
            or is_redis_error(error)
        ):
            return MemoryInvocationError(
                MemoryErrorCode.MEMORY_UNAVAILABLE,
                "memory provider is unreachable",
                details={"operation": operation, "cause_type": type(error).__name__},
                cause=error,
            )
        if isinstance(error, (ValueError, TypeError)):
            return MemoryInvocationError(
                MemoryErrorCode.RECORD_REJECTED,
                "memory provider returned invalid data",
                details={"operation": operation, "cause_type": type(error).__name__},
                cause=error,
            )
        return MemoryInvocationError(
            MemoryErrorCode.UNKNOWN_MEMORY_ERROR,
            REDACTED_VALUE,
            details={"operation": operation, "cause_type": type(error).__name__},
            cause=error,
        )
