"""Current-turn context and recall projection into the provider context.

Every turn revalidates recalled records against the trusted current-turn
context: tenant scope, policy/catalog fingerprints, semantic view,
adapter/artifact references, and expiry.  Recalled memory becomes bounded
protected references in the P2.1 provider context - never raw prompts,
queries, or result material.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nl2data.models import QueryRequest
from nl2data_core.ai.context import (
    AuthorizedModelContext,
    SemanticReference,
    assemble_model_context,
)
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.governance.models import PolicyScope
from nl2data_core.memory.models import (
    MemoryRecallProjection,
    MemoryRecord,
    MemoryScope,
    QueryReferencePayload,
    SemanticDecisionPayload,
)
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.tenancy.models import TenantScopeContext

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

#: Bounded number of projected references and stale ids per turn.
_MAX_MEMORY_REFERENCES = 100
_MAX_STALE_REFERENCES = 100


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _view_fingerprint(view: AuthorizedView | None) -> str | None:
    if view is None:
        return None
    return sha256_fingerprint(
        {
            "source_id": view.source_id,
            "root_entity_ids": sorted(view.root_entity_ids),
            "field_ids": sorted(view.field_ids),
            "catalog_fingerprint": view.catalog_fingerprint,
        }
    )


class CurrentTurnContext(BaseModel):
    """Immutable trusted context of the current turn.

    Built only from host integration input (tenant scope, policy scope,
    authorized view, adapter identity); never from public request bodies
    or prompts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    conversation_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    tenant_scope_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    policy_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    catalog_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    semantic_view_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    source_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    adapter_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    artifact_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> CurrentTurnContext:
        object.__setattr__(self, "fingerprint", sha256_fingerprint(self.safe_payload()))
        return self

    def safe_payload(self) -> dict[str, Any]:
        """Serialize with fingerprints and bounded identifiers only."""
        return {
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "tenant_scope_fingerprint": self.tenant_scope_fingerprint,
            "policy_fingerprint": self.policy_fingerprint,
            "catalog_fingerprint": self.catalog_fingerprint,
            "semantic_view_fingerprint": self.semantic_view_fingerprint,
            "source_id": self.source_id,
            "adapter_id": self.adapter_id,
            "artifact_fingerprint": self.artifact_fingerprint,
        }

    def recall_scope(self) -> MemoryScope:
        """The provider recall scope derived from this turn."""
        return MemoryScope(
            tenant_scope_fingerprint=self.tenant_scope_fingerprint,
            session_id=self.session_id,
            conversation_id=self.conversation_id,
            adapter_id=self.adapter_id,
            source_id=self.source_id,
        )


def build_current_turn_context(
    *,
    session_id: str,
    conversation_id: str | None = None,
    tenant_scope: TenantScopeContext | None = None,
    view: AuthorizedView | None = None,
    policy_scope: PolicyScope | None = None,
    adapter_id: str | None = None,
    artifact_fingerprint: str | None = None,
) -> CurrentTurnContext:
    """Build the trusted current-turn context from host integration input."""
    return CurrentTurnContext(
        session_id=session_id,
        conversation_id=conversation_id,
        tenant_scope_fingerprint=(
            tenant_scope.scope_fingerprint if tenant_scope is not None else None
        ),
        policy_fingerprint=(
            policy_scope.policy_fingerprint if policy_scope is not None else None
        ),
        catalog_fingerprint=view.catalog_fingerprint if view is not None else None,
        semantic_view_fingerprint=_view_fingerprint(view),
        source_id=view.source_id if view is not None else None,
        adapter_id=adapter_id,
        artifact_fingerprint=artifact_fingerprint,
    )


class MemoryReference(BaseModel):
    """One protected reference projected into the provider context.

    Carries only bounded identifiers and fingerprints; the recalled record
    fingerprint is the stable link back to memory, never raw content.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference_kind: Literal["query_reference", "semantic_decision"]
    reference_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    policy_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    catalog_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    semantic_view_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    adapter_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    source_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    root_entity_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    field_ids: frozenset[str] = Field(default_factory=frozenset)
    confirmed_interpretation: str | None = Field(default=None, max_length=2000)
    record_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> MemoryReference:
        object.__setattr__(
            self,
            "fingerprint",
            sha256_fingerprint(
                {
                    "reference_kind": self.reference_kind,
                    "reference_id": self.reference_id,
                    "policy_fingerprint": self.policy_fingerprint,
                    "catalog_fingerprint": self.catalog_fingerprint,
                    "semantic_view_fingerprint": self.semantic_view_fingerprint,
                    "adapter_id": self.adapter_id,
                    "source_id": self.source_id,
                    "root_entity_id": self.root_entity_id,
                    "field_ids": sorted(self.field_ids),
                    "confirmed_interpretation": self.confirmed_interpretation,
                    "record_fingerprint": self.record_fingerprint,
                }
            ),
        )
        return self

    def safe_payload(self) -> dict[str, Any]:
        """Serialize with bounded identifiers and fingerprints only."""
        return {
            "reference_kind": self.reference_kind,
            "reference_id": self.reference_id,
            "policy_fingerprint": self.policy_fingerprint,
            "catalog_fingerprint": self.catalog_fingerprint,
            "semantic_view_fingerprint": self.semantic_view_fingerprint,
            "adapter_id": self.adapter_id,
            "source_id": self.source_id,
            "root_entity_id": self.root_entity_id,
            "field_ids": sorted(self.field_ids),
            "confirmed_interpretation": self.confirmed_interpretation,
            "record_fingerprint": self.record_fingerprint,
        }


class MemoryContextProjection(BaseModel):
    """The P2.1 provider context enriched with protected memory references.

    The outer fingerprint covers the base context fingerprint plus the
    projected reference fingerprints and stale ids - never raw content.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_context: AuthorizedModelContext
    memory_references: tuple[MemoryReference, ...] = Field(
        default_factory=tuple, max_length=_MAX_MEMORY_REFERENCES
    )
    stale_reference_ids: tuple[str, ...] = Field(
        default_factory=tuple, max_length=_MAX_STALE_REFERENCES
    )
    memory_unavailable: bool = False
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> MemoryContextProjection:
        fingerprint = sha256_fingerprint(
            {
                "model_context_fingerprint": self.model_context.fingerprint,
                "memory_references": [
                    reference.fingerprint for reference in self.memory_references
                ],
                "stale_reference_ids": sorted(self.stale_reference_ids),
                "memory_unavailable": self.memory_unavailable,
            }
        )
        object.__setattr__(self, "fingerprint", fingerprint)
        return self

    def safe_payload(self) -> dict[str, Any]:
        """Provider-safe payload: base context plus protected references."""
        return {
            **self.model_context.safe_payload(),
            "memory": {
                "references": [
                    reference.safe_payload() for reference in self.memory_references
                ],
                "stale_reference_ids": sorted(self.stale_reference_ids),
                "memory_unavailable": self.memory_unavailable,
                "fingerprint": self.fingerprint,
            },
        }


def _project_record(
    record: MemoryRecord,
    *,
    view: AuthorizedView,
    turn: CurrentTurnContext,
    now: datetime,
) -> tuple[MemoryReference | None, bool]:
    """Project one record into a protected reference.

    Returns ``(reference, stale)``; ``stale`` marks records that fail
    per-turn revalidation and must never reach the provider context.
    Non-projectable kinds (working/session/audit) are simply ignored.
    """
    if record.is_expired(now=now):
        return None, True
    payload = record.payload
    if isinstance(payload, QueryReferencePayload):
        reference = payload.reference
        if (
            record.scope.tenant_scope_fingerprint != turn.tenant_scope_fingerprint
            or reference.policy_fingerprint != turn.policy_fingerprint
            or reference.catalog_fingerprint != turn.catalog_fingerprint
            or reference.semantic_view_fingerprint != turn.semantic_view_fingerprint
            or reference.adapter_id != turn.adapter_id
            or reference.source_id != view.source_id
            or (view.root_entity_ids and reference.root_entity_id not in view.root_entity_ids)
            or (
                reference.field_ids
                and not (reference.field_ids & view.field_ids)
            )
        ):
            return None, True
        return (
            MemoryReference(
                reference_kind="query_reference",
                reference_id=reference.reference_id,
                policy_fingerprint=reference.policy_fingerprint,
                catalog_fingerprint=reference.catalog_fingerprint,
                semantic_view_fingerprint=reference.semantic_view_fingerprint,
                adapter_id=reference.adapter_id,
                source_id=reference.source_id,
                root_entity_id=reference.root_entity_id,
                field_ids=reference.field_ids,
                record_fingerprint=record.fingerprint,
            ),
            False,
        )
    if isinstance(payload, SemanticDecisionPayload):
        decision = payload.decision
        if (
            (turn.policy_fingerprint is not None
                and decision.policy_fingerprint != turn.policy_fingerprint)
            or (turn.catalog_fingerprint is not None
                and decision.catalog_fingerprint != turn.catalog_fingerprint)
        ):
            return None, True
        return (
            MemoryReference(
                reference_kind="semantic_decision",
                reference_id=decision.decision_id,
                policy_fingerprint=decision.policy_fingerprint,
                catalog_fingerprint=decision.catalog_fingerprint,
                confirmed_interpretation=decision.confirmed_interpretation,
                record_fingerprint=record.fingerprint,
            ),
            False,
        )
    return None, False


def project_recall_context(
    *,
    request: QueryRequest,
    view: AuthorizedView,
    semantic_references: dict[str, SemanticReference] | None = None,
    turn: CurrentTurnContext,
    projection: MemoryRecallProjection,
    max_output_tokens: int = 4096,
    now: datetime | None = None,
) -> MemoryContextProjection:
    """Project recalled records into the authorized provider context.

    Every record is revalidated against the current turn; stale records
    are reported by id and never projected.  Working/session/audit records
    carry no provider-facing reference and are ignored.
    """
    model_context = assemble_model_context(
        request=request,
        view=view,
        semantic_references=semantic_references,
        max_output_tokens=max_output_tokens,
    )
    references: list[MemoryReference] = []
    stale_ids: list[str] = []
    revalidate_at = now or _utc_now()
    for record in projection.records:
        reference, stale = _project_record(
            record, view=view, turn=turn, now=revalidate_at
        )
        if stale:
            stale_ids.append(record.record_id)
        elif reference is not None:
            references.append(reference)
    return MemoryContextProjection(
        model_context=model_context,
        memory_references=tuple(references[:_MAX_MEMORY_REFERENCES]),
        stale_reference_ids=tuple(stale_ids[:_MAX_STALE_REFERENCES]),
    )
