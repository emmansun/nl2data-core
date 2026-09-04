"""Multi-turn context resolution with stateless safe degradation.

The resolver recalls protected records, revalidates them per turn, and
produces one of four bounded outcomes: ``PROJECTED`` (recalled context
flows into the provider context), ``STATELESS`` (resolve as if Memory did
not exist), ``CLARIFICATION`` (the prompt depends on missing or stale
history), or ``REJECTED`` (a non-availability memory error).  Recalled
plans are never executed directly - memory is context only.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nl2data.models import QueryRequest
from nl2data_core.ai.context import SemanticReference
from nl2data_core.canonical import strict_sha256_fingerprint
from nl2data_core.memory.context import (
    CurrentTurnContext,
    MemoryContextProjection,
    project_recall_context,
)
from nl2data_core.memory.errors import (
    MemoryErrorCode,
    MemoryInvocationError,
)
from nl2data_core.memory.models import (
    MemoryRecallBudget,
    MemoryRecallProjection,
    MemoryRecord,
    MemoryScope,
    QueryReference,
    QueryReferencePayload,
)
from nl2data_core.memory.protocol import MemoryProvider
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.views.projection import ResolvedViewProjection

#: Bounded markers that signal dependence on prior conversation context.
_FOLLOW_UP_MARKERS = (
    "previous",
    "prior",
    "earlier",
    "before",
    "last time",
    "same as",
    "same query",
    "same request",
    "again",
    "as before",
    "that query",
    "that request",
    "follow-up",
    "follow up",
    "previously",
)


def _depends_on_prior_context(prompt: str) -> bool:
    """Heuristic for follow-up dependence (defense in depth only)."""
    lowered = prompt.lower()
    return any(marker in lowered for marker in _FOLLOW_UP_MARKERS)


class MultiTurnResolutionKind(StrEnum):
    """Bounded outcomes of one multi-turn resolution."""

    PROJECTED = "projected"
    STATELESS = "stateless"
    CLARIFICATION = "clarification"
    REJECTED = "rejected"


class MultiTurnResolution(BaseModel):
    """Immutable result of one multi-turn resolution attempt.

    A ``PROJECTED`` resolution carries the enriched provider context;
    every other kind carries no projection.  ``reason`` is bounded and
    never contains raw prompt or query material.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: MultiTurnResolutionKind
    projection: MemoryContextProjection | None = None
    memory_unavailable: bool = False
    stale_reference_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    reason: str = Field(min_length=1, max_length=512)
    fingerprint: str = Field(default="", pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _consistent(self) -> MultiTurnResolution:
        if self.kind is MultiTurnResolutionKind.PROJECTED and self.projection is None:
            raise ValueError("projected resolutions must carry a projection")
        if (
            self.kind is not MultiTurnResolutionKind.PROJECTED
            and self.projection is not None
        ):
            raise ValueError("non-projected resolutions must not carry a projection")
        return self

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> MultiTurnResolution:
        fingerprint = strict_sha256_fingerprint(
            {
                "kind": self.kind.value,
                "projection": self.projection.fingerprint if self.projection else None,
                "memory_unavailable": self.memory_unavailable,
                "stale_reference_ids": sorted(self.stale_reference_ids),
                "reason": self.reason,
            }
        )
        object.__setattr__(self, "fingerprint", fingerprint)
        return self

    def safe_payload(self) -> dict[str, Any]:
        """Serialize with bounded codes, references, and reason only."""
        return {
            "kind": self.kind.value,
            "projection": self.projection.safe_payload() if self.projection else None,
            "memory_unavailable": self.memory_unavailable,
            "stale_reference_ids": sorted(self.stale_reference_ids),
            "reason": self.reason,
        }


class MultiTurnResolver:
    """Resolves one request with recalled context or a safe fallback."""

    def __init__(
        self,
        *,
        provider: MemoryProvider | None,
        view: AuthorizedView,
        semantic_references: dict[str, SemanticReference] | None = None,
        turn: CurrentTurnContext,
        recall_budget: MemoryRecallBudget | None = None,
        now: datetime | None = None,
        resolved_view: ResolvedViewProjection | None = None,
    ) -> None:
        self._provider = provider
        self._view = view
        self._references = dict(semantic_references or {})
        self._turn = turn
        self._recall_budget = recall_budget
        self._now = now
        self._resolved_view = resolved_view

    def resolve(self, request: QueryRequest) -> MultiTurnResolution:
        """Resolve one request, degrading statelessly when memory is absent."""
        depends = _depends_on_prior_context(request.prompt)
        provider = self._provider
        if provider is None:
            return self._unavailable(depends)
        try:
            if not provider.is_available():
                return self._unavailable(depends)
            projection = provider.recall(
                scope=self._turn.recall_scope(), budget=self._recall_budget
            )
        except MemoryInvocationError as error:
            if error.code is MemoryErrorCode.MEMORY_UNAVAILABLE:
                return self._unavailable(depends)
            return MultiTurnResolution(
                kind=MultiTurnResolutionKind.REJECTED,
                memory_unavailable=False,
                reason=f"memory resolution failed: {error.code.value}",
            )
        except Exception:
            # Any provider failure degrades statelessly; memory never fails a query.
            return self._unavailable(depends)
        return self._project(request, projection, depends)

    def _unavailable(self, depends: bool) -> MultiTurnResolution:
        if depends:
            return MultiTurnResolution(
                kind=MultiTurnResolutionKind.CLARIFICATION,
                memory_unavailable=True,
                reason="memory is unavailable and the request depends on prior context",
            )
        return MultiTurnResolution(
            kind=MultiTurnResolutionKind.STATELESS,
            memory_unavailable=True,
            reason="memory is unavailable; resolving statelessly",
        )

    def _project(
        self,
        request: QueryRequest,
        projection: MemoryRecallProjection,
        depends: bool,
    ) -> MultiTurnResolution:
        projected = project_recall_context(
            request=request,
            view=self._view,
            semantic_references=self._references,
            turn=self._turn,
            projection=projection,
            now=self._now,
            resolved_view=self._resolved_view,
        )
        if projected.stale_reference_ids:
            if depends:
                return MultiTurnResolution(
                    kind=MultiTurnResolutionKind.CLARIFICATION,
                    stale_reference_ids=projected.stale_reference_ids,
                    reason="prior references are stale; clarification required",
                )
            return MultiTurnResolution(
                kind=MultiTurnResolutionKind.STATELESS,
                stale_reference_ids=projected.stale_reference_ids,
                reason="prior references are stale; resolving statelessly",
            )
        if not projected.memory_references:
            if depends:
                return MultiTurnResolution(
                    kind=MultiTurnResolutionKind.CLARIFICATION,
                    reason="no usable prior context for a follow-up request",
                )
            return MultiTurnResolution(
                kind=MultiTurnResolutionKind.STATELESS,
                reason="no prior context; resolving statelessly",
            )
        return MultiTurnResolution(
            kind=MultiTurnResolutionKind.PROJECTED,
            projection=projected,
            reason="recalled context projected into the provider context",
        )


def record_query_reference(
    *,
    provider: MemoryProvider,
    turn: CurrentTurnContext,
    intent_fingerprint: str | None = None,
    ir_fingerprint: str | None = None,
    artifact_fingerprint: str | None = None,
    source_id: str,
    root_entity_id: str | None = None,
    field_ids: frozenset[str] = frozenset(),
    ttl_seconds: int = 86_400,
) -> str:
    """Record a logical query reference after a successful turn.

    The reference id is a deterministic digest of the IR/intent
    fingerprints so equal queries never create duplicate references; raw
    prompts and queries are never stored.
    """
    reference_id = strict_sha256_fingerprint(
        {"ir": ir_fingerprint, "intent": intent_fingerprint}
    )[7:23]
    if turn.tenant_scope_fingerprint is None:
        raise MemoryInvocationError(
            MemoryErrorCode.RECORD_REJECTED,
            "query references require a tenant scope fingerprint",
        )
    record = MemoryRecord(
        record_id=f"mem-{reference_id}",
        scope=MemoryScope(
            tenant_scope_fingerprint=turn.tenant_scope_fingerprint,
            session_id=turn.session_id,
            conversation_id=turn.conversation_id,
            adapter_id=turn.adapter_id,
            source_id=source_id,
        ),
        payload=QueryReferencePayload(
            reference=QueryReference(
                reference_id=reference_id,
                intent_fingerprint=intent_fingerprint,
                ir_fingerprint=ir_fingerprint,
                artifact_fingerprint=artifact_fingerprint,
                policy_fingerprint=turn.policy_fingerprint,
                catalog_fingerprint=turn.catalog_fingerprint,
                semantic_view_fingerprint=turn.semantic_view_fingerprint,
                adapter_id=turn.adapter_id,
                source_id=source_id,
                root_entity_id=root_entity_id,
                field_ids=field_ids,
            )
        ),
        ttl_seconds=ttl_seconds,
    )
    return provider.append(record)
