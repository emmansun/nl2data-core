"""Bounded assembly audit-evidence contracts for the semantic control plane.

The assembly lifecycle (authoring/import, lint, assertion review, draft
approval, verification, publication, activation, rollback) is represented
here as immutable, safe audit-evidence entries linked into a coherent,
queryable trail.  Entries are summaries of *completed* lifecycle actions:
they never grant lifecycle authority, never enter semantic Bundle
canonical payloads, and never carry raw prompts, SQL/MQL, physical names,
credentials, resolved deployment values, native objects, unrestricted
sample values, raw backend exceptions, or raw operator identities.

Operator identity is an opaque, host-provided audit reference.  Evidence
facts (assertion ids, payload hashes, fingerprints, policy profiles,
statuses) are bounded system facts validated here so the same contracts
can cross the Admin inspection and durable-catalog boundaries without
untyped dictionaries.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nl2data_core.canonical import strict_sha256_fingerprint
from nl2data_core.views.models import validate_safe_description

FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
ISSUE_CODE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
#: Assertion ids are content fingerprints; most other references are identifiers.
REFERENCE_PATTERN = (
    r"^(sha256:[0-9a-f]{64}|[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127})$"
)

MAX_REFERENCE_CHARS = 256
MAX_REASON_CHARS = 1_024
MAX_PREDECESSOR_LINKS = 8
#: Bounded maximum number of entries returned by one trail page.
MAX_TRAIL_ENTRIES = 200

#: Schema version of the audit-evidence contract itself.
AUDIT_EVIDENCE_SCHEMA_VERSION: Literal[1] = 1


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AuditEventKind(StrEnum):
    """Bounded kinds of completed assembly lifecycle actions."""

    AUTHORING_IMPORT = "authoring_import"
    ASSERTION_REVIEW = "assertion_review"
    ASSERTION_EDIT = "assertion_edit"
    ASSERTION_APPROVAL = "assertion_approval"
    DRAFT_APPROVAL = "draft_approval"
    LINT_REFERENCE = "lint_reference"
    VERIFICATION_REFERENCE = "verification_reference"
    PUBLICATION = "publication"
    ACTIVATION = "activation"
    ROLLBACK = "rollback"


class AuditSubjectKind(StrEnum):
    """Bounded subject kinds an audit-evidence entry can be filed under."""

    DRAFT = "draft"
    ASSERTION = "assertion"
    BUNDLE = "bundle"
    PUBLICATION = "publication"
    ACTIVATION = "activation"
    ROLLBACK = "rollback"


class AuditOutcome(StrEnum):
    """Safe outcome of the completed lifecycle action."""

    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"


class AuditPayloadBindings(BaseModel):
    """Typed fingerprint/policy facts bound into one audit-evidence entry.

    Every field is optional and bounded; free-form payload dictionaries are
    deliberately impossible so unsafe material cannot cross boundaries.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reviewed_payload_hash: str | None = Field(default=None, pattern=FINGERPRINT_PATTERN)
    resulting_payload_hash: str | None = Field(
        default=None, pattern=FINGERPRINT_PATTERN
    )
    plan_fingerprint: str | None = Field(default=None, pattern=FINGERPRINT_PATTERN)
    manifest_fingerprint: str | None = Field(default=None, pattern=FINGERPRINT_PATTERN)
    evidence_fingerprint: str | None = Field(default=None, pattern=FINGERPRINT_PATTERN)
    release_binding_fingerprint: str | None = Field(
        default=None, pattern=FINGERPRINT_PATTERN
    )
    prior_active_fingerprint: str | None = Field(
        default=None, pattern=FINGERPRINT_PATTERN
    )
    resulting_active_fingerprint: str | None = Field(
        default=None, pattern=FINGERPRINT_PATTERN
    )
    policy_profile: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    policy_version: int | None = Field(default=None, ge=1, le=1_000)
    policy_fingerprint: str | None = Field(default=None, pattern=FINGERPRINT_PATTERN)
    lint_reference: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    publish_audit_reference: str | None = Field(
        default=None, pattern=IDENTIFIER_PATTERN
    )
    separation_mode: str | None = Field(default=None, min_length=1, max_length=64)
    separation_allowed: bool | None = None

    def bound_facts(self) -> dict[str, Any]:
        """Only the set facts, canonically ordered, for fingerprints."""
        payload = self.model_dump(mode="json", exclude_none=True)
        return dict(sorted(payload.items()))


class AssemblyAuditEvidenceEntry(BaseModel):
    """One immutable, bounded audit-evidence entry for a lifecycle action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = AUDIT_EVIDENCE_SCHEMA_VERSION
    event_id: str = Field(pattern=IDENTIFIER_PATTERN)
    event_kind: AuditEventKind
    subject_kind: AuditSubjectKind
    subject_reference: str = Field(
        min_length=1, max_length=MAX_REFERENCE_CHARS, pattern=REFERENCE_PATTERN
    )
    tenant_scope_fingerprint: str = Field(pattern=FINGERPRINT_PATTERN)
    source_scope_fingerprint: str = Field(pattern=FINGERPRINT_PATTERN)
    draft_id: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    draft_revision: int | None = Field(default=None, ge=0)
    assertion_id: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    bundle_fingerprint: str | None = Field(default=None, pattern=FINGERPRINT_PATTERN)
    lifecycle_reference: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    outcome: AuditOutcome = AuditOutcome.SUCCEEDED
    status_code: str | None = Field(default=None, pattern=ISSUE_CODE_PATTERN)
    operator_audit_reference: str | None = Field(
        default=None, min_length=1, max_length=MAX_REFERENCE_CHARS
    )
    reason: str = Field(default="", max_length=MAX_REASON_CHARS)
    payload_bindings: AuditPayloadBindings = Field(default_factory=AuditPayloadBindings)
    predecessor_event_ids: tuple[str, ...] = Field(
        default_factory=tuple, max_length=MAX_PREDECESSOR_LINKS
    )
    occurred_at: datetime = Field(default_factory=_utc_now)
    fingerprint: str = Field(default="", pattern=FINGERPRINT_PATTERN)

    @field_validator("operator_audit_reference", "reason")
    @classmethod
    def _safe_text(cls, value: str | None) -> str | None:
        return validate_safe_description(value) if value else value

    @field_validator("subject_reference")
    @classmethod
    def _safe_subject(cls, value: str) -> str:
        if not value.startswith(("sha256:",)) and not value[0].isalnum():
            raise ValueError(
                "audit subject references must be identifiers or fingerprints"
            )
        return value

    @field_validator("predecessor_event_ids")
    @classmethod
    def _bounded_predecessors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("audit predecessor references must be unique")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def _validate_and_fingerprint(self) -> AssemblyAuditEvidenceEntry:
        if self.event_id in self.predecessor_event_ids:
            raise ValueError("audit entries cannot be their own predecessor")
        self._check_subject_consistency()
        object.__setattr__(
            self, "fingerprint", strict_sha256_fingerprint(self.canonical_payload())
        )
        return self

    def _check_subject_consistency(self) -> None:
        kind = self.event_kind
        if kind in {
            AuditEventKind.ASSERTION_REVIEW,
            AuditEventKind.ASSERTION_EDIT,
            AuditEventKind.ASSERTION_APPROVAL,
        }:
            if self.assertion_id is None or self.draft_id is None:
                raise ValueError(
                    "assertion audit entries require an assertion and draft reference"
                )
            if self.subject_kind is not AuditSubjectKind.ASSERTION:
                raise ValueError("assertion audit entries must be filed under assertions")
            return
        if kind in {AuditEventKind.AUTHORING_IMPORT, AuditEventKind.DRAFT_APPROVAL}:
            if self.draft_id is None:
                raise ValueError("draft audit entries require a draft reference")
            if self.subject_kind is not AuditSubjectKind.DRAFT:
                raise ValueError("draft audit entries must be filed under drafts")
            return
        if kind in {AuditEventKind.LINT_REFERENCE, AuditEventKind.VERIFICATION_REFERENCE}:
            if self.draft_id is None:
                raise ValueError(
                    "lint and verification evidence entries require a draft reference"
                )
            if self.subject_kind is not AuditSubjectKind.DRAFT:
                raise ValueError(
                    "lint and verification evidence entries must be filed under drafts"
                )
            return
        # Publication, activation, and rollback entries are filed under
        # immutable published artifacts.
        if self.bundle_fingerprint is None or self.lifecycle_reference is None:
            raise ValueError(
                "publication, activation, and rollback audit entries require a "
                "bundle fingerprint and lifecycle reference"
            )
        if self.subject_kind is not AuditSubjectKind(kind.value):
            raise ValueError("lifecycle audit entries must be filed under their subject")

    def canonical_payload(self) -> dict[str, Any]:
        """Canonical fingerprint payload excluding presentation metadata.

        ``occurred_at`` is deliberately excluded: it is presentation
        metadata for ordering, not an identity fact, so entry fingerprints
        stay stable across clock skew between workers.  No duration or
        other volatile measurement exists on entries by construction.
        """
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "event_kind": self.event_kind.value,
            "subject_kind": self.subject_kind.value,
            "subject_reference": self.subject_reference,
            "tenant_scope_fingerprint": self.tenant_scope_fingerprint,
            "source_scope_fingerprint": self.source_scope_fingerprint,
            "outcome": self.outcome.value,
            "payload_bindings": self.payload_bindings.bound_facts(),
            "predecessor_event_ids": list(self.predecessor_event_ids),
        }
        for field in (
            "draft_id",
            "draft_revision",
            "assertion_id",
            "bundle_fingerprint",
            "lifecycle_reference",
            "status_code",
            "operator_audit_reference",
        ):
            value = getattr(self, field)
            if value is not None:
                payload[field] = value
        if self.reason:
            payload["reason"] = self.reason
        return payload

    def safe_payload(self) -> dict[str, Any]:
        """Bounded JSON projection safe for inspection surfaces."""
        return self.model_dump(mode="json")

    def verify_fingerprint(self) -> bool:
        """Whether the stored entry fingerprint still matches its facts."""
        return self.fingerprint == strict_sha256_fingerprint(self.canonical_payload())


class PublicationAuditEvidence(BaseModel):
    """Immutable publication-time binding of release readiness inputs.

    Publication audit evidence explains why one semantic Bundle became
    authoritative: it links the approved draft revision, verification plan
    and policy, accepted-assertion manifest, Verification Suite evidence,
    lint readiness reference when present, separation-of-duties result,
    publish audit reference, tenant/source scope, and the immutable Bundle
    fingerprint.  It is validated against the publication aggregate before
    catalog persistence and never enters the Bundle fingerprint domain.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = AUDIT_EVIDENCE_SCHEMA_VERSION
    approved_draft_id: str = Field(pattern=IDENTIFIER_PATTERN)
    approved_draft_revision: int = Field(ge=1)
    approved_plan_fingerprint: str | None = Field(
        default=None, pattern=FINGERPRINT_PATTERN
    )
    bundle_fingerprint: str = Field(pattern=FINGERPRINT_PATTERN)
    manifest_fingerprint: str = Field(pattern=FINGERPRINT_PATTERN)
    verification_evidence_fingerprint: str = Field(pattern=FINGERPRINT_PATTERN)
    tenant_scope_fingerprint: str = Field(pattern=FINGERPRINT_PATTERN)
    source_scope_fingerprint: str = Field(pattern=FINGERPRINT_PATTERN)
    policy_profile: str = Field(pattern=IDENTIFIER_PATTERN)
    policy_version: int = Field(ge=1, le=1_000)
    policy_fingerprint: str = Field(pattern=FINGERPRINT_PATTERN)
    lint_reference: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    separation_mode: str = Field(min_length=1, max_length=64)
    separation_allowed: bool
    separation_reason_code: str | None = Field(default=None, min_length=1, max_length=64)
    publish_audit_reference: str = Field(pattern=IDENTIFIER_PATTERN)
    fingerprint: str = Field(default="", pattern=FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def _validate_and_fingerprint(self) -> PublicationAuditEvidence:
        object.__setattr__(
            self, "fingerprint", strict_sha256_fingerprint(self.canonical_payload())
        )
        return self

    def canonical_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "approved_draft_id": self.approved_draft_id,
            "approved_draft_revision": self.approved_draft_revision,
            "bundle_fingerprint": self.bundle_fingerprint,
            "manifest_fingerprint": self.manifest_fingerprint,
            "verification_evidence_fingerprint": (
                self.verification_evidence_fingerprint
            ),
            "tenant_scope_fingerprint": self.tenant_scope_fingerprint,
            "source_scope_fingerprint": self.source_scope_fingerprint,
            "policy_profile": self.policy_profile,
            "policy_version": self.policy_version,
            "policy_fingerprint": self.policy_fingerprint,
            "separation_mode": self.separation_mode,
            "separation_allowed": self.separation_allowed,
            "publish_audit_reference": self.publish_audit_reference,
        }
        if self.approved_plan_fingerprint is not None:
            payload["approved_plan_fingerprint"] = self.approved_plan_fingerprint
        if self.lint_reference is not None:
            payload["lint_reference"] = self.lint_reference
        if self.separation_reason_code is not None:
            payload["separation_reason_code"] = self.separation_reason_code
        return payload

    def publication_event_id(self) -> str:
        """Deterministic event id for the publication entry of this binding."""
        return "publish-" + self.fingerprint.removeprefix("sha256:")[:24]


class AuditEvidenceRecordPort(Protocol):
    """Core catalog port for persisting and querying audit-evidence entries.

    Implementations (in-memory reference catalog, durable PostgreSQL
    catalog) store versioned envelopes, reject tampered or unsafe entries,
    scope every lookup by tenant, and return deterministic bounded trails.
    """

    def record_audit_entries(
        self,
        entries: Sequence[AssemblyAuditEvidenceEntry],
        *,
        tenant_scope_fingerprint: str,
    ) -> None: ...

    def audit_entries(
        self,
        *,
        tenant_scope_fingerprint: str | None = None,
        draft_id: str | None = None,
        draft_revision_min: int | None = None,
        draft_revision_max: int | None = None,
        assertion_id: str | None = None,
        bundle_fingerprint: str | None = None,
        lifecycle_reference: str | None = None,
        predecessor_event_id: str | None = None,
        limit: int = MAX_TRAIL_ENTRIES,
        cursor: str | None = None,
    ) -> AuditTrail: ...


class AuditTrail(BaseModel):
    """One deterministic, bounded page of an audit-evidence trail."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entries: tuple[AssemblyAuditEvidenceEntry, ...] = Field(default_factory=tuple)
    total_count: int = Field(default=0, ge=0)
    next_cursor: str | None = None
    has_more: bool = False

    @model_validator(mode="after")
    def _bounded_page(self) -> AuditTrail:
        if len(self.entries) > MAX_TRAIL_ENTRIES:
            raise ValueError("audit trail pages are bounded")
        if self.next_cursor is not None:
            if not self.entries or self.next_cursor != self.entries[-1].event_id:
                raise ValueError("audit trail cursors must name the last page entry")
            if not self.has_more:
                raise ValueError("audit trail cursors require more matching entries")
        return self


def order_audit_entries(
    entries: Iterable[AssemblyAuditEvidenceEntry],
) -> tuple[AssemblyAuditEvidenceEntry, ...]:
    """Deterministic lifecycle ordering: by occurrence time, then event id."""
    return tuple(sorted(entries, key=lambda entry: (entry.occurred_at, entry.event_id)))


def bounded_audit_trail(
    entries: Iterable[AssemblyAuditEvidenceEntry],
    *,
    limit: int = MAX_TRAIL_ENTRIES,
    cursor: str | None = None,
) -> AuditTrail:
    """Order, bound, and paginate audit entries into one trail page.

    The cursor is the last returned event id; a page continues after that
    entry in the deterministic ordering.  Unknown cursors restart from the
    beginning rather than failing, so pruned history never breaks paging.
    """
    ordered = order_audit_entries(entries)
    if limit < 1 or limit > MAX_TRAIL_ENTRIES:
        raise ValueError("audit trail limit must be between 1 and the maximum")
    if cursor is not None:
        index = next(
            (
                position
                for position, entry in enumerate(ordered)
                if entry.event_id == cursor
            ),
            -1,
        )
        ordered = ordered[index + 1 :] if index >= 0 else ordered
    page = ordered[:limit]
    has_more = len(ordered) > len(page)
    return AuditTrail(
        entries=page,
        total_count=len(ordered),
        next_cursor=(page[-1].event_id if page and has_more else None),
        has_more=has_more,
    )


def redact_audit_entry(
    entry: AssemblyAuditEvidenceEntry,
    *,
    max_text_chars: int = 256,
) -> dict[str, Any]:
    """Truncate free-text fields of an entry projection for inspection."""
    payload = entry.safe_payload()
    reason = payload.get("reason")
    if isinstance(reason, str) and len(reason) > max_text_chars:
        payload["reason"] = reason[:max_text_chars]
    return payload


# -- lifecycle record factories -------------------------------------------------

def assertion_decision_audit_entry(
    record: Any,
    *,
    tenant_scope_fingerprint: str,
    source_scope_fingerprint: str,
    event_id: str,
    draft_revision: int | None = None,
    predecessor_event_ids: Sequence[str] = (),
    occurred_at: datetime | None = None,
    reason: str = "",
) -> AssemblyAuditEvidenceEntry:
    """Bind one completed assertion review/edit/approval decision as evidence.

    The entry links the assertion id, reviewed and resulting payload hashes,
    draft reference, decision outcome, and the (already opaque) operator
    audit reference carried by the lifecycle record.  It never carries the
    reviewed payload material itself.
    """
    from .lifecycle import AssertionDecisionKind, AssertionDecisionRecord

    if not isinstance(record, AssertionDecisionRecord):
        raise ValueError("expected an assertion decision lifecycle record")
    kind_map = {
        AssertionDecisionKind.APPROVE: AuditEventKind.ASSERTION_APPROVAL,
        AssertionDecisionKind.REJECT: AuditEventKind.ASSERTION_REVIEW,
        AssertionDecisionKind.EDIT: AuditEventKind.ASSERTION_EDIT,
    }
    outcome = (
        AuditOutcome.SUCCEEDED
        if record.action is not AssertionDecisionKind.REJECT
        else AuditOutcome.REJECTED
    )
    return AssemblyAuditEvidenceEntry(
        event_id=event_id,
        event_kind=kind_map[record.action],
        subject_kind=AuditSubjectKind.ASSERTION,
        subject_reference=record.assertion_id,
        tenant_scope_fingerprint=tenant_scope_fingerprint,
        source_scope_fingerprint=source_scope_fingerprint,
        draft_id=record.draft_id,
        draft_revision=draft_revision,
        assertion_id=record.assertion_id,
        outcome=outcome,
        operator_audit_reference=record.operator_reference,
        reason=reason or record.reason,
        payload_bindings=AuditPayloadBindings(
            reviewed_payload_hash=record.previous_payload_hash,
            resulting_payload_hash=record.resulting_payload_hash,
        ),
        predecessor_event_ids=tuple(predecessor_event_ids),
        occurred_at=occurred_at or record.occurred_at,
    )


def draft_lifecycle_audit_entry(
    record: Any,
    *,
    tenant_scope_fingerprint: str,
    source_scope_fingerprint: str,
    event_id: str,
    predecessor_event_ids: Sequence[str] = (),
    occurred_at: datetime | None = None,
) -> AssemblyAuditEvidenceEntry:
    """Bind one completed draft submit/approve transition as evidence.

    Submission closes the authoring/import phase for the frozen revision,
    so it is classified as ``authoring_import``; approval is its own event
    kind.  Only bounded revision facts are carried.
    """
    from .lifecycle import DraftLifecycleAction, DraftLifecycleRecord

    if not isinstance(record, DraftLifecycleRecord):
        raise ValueError("expected a draft lifecycle record")
    kind = (
        AuditEventKind.AUTHORING_IMPORT
        if record.action is DraftLifecycleAction.SUBMIT_FOR_REVIEW
        else AuditEventKind.DRAFT_APPROVAL
    )
    return AssemblyAuditEvidenceEntry(
        event_id=event_id,
        event_kind=kind,
        subject_kind=AuditSubjectKind.DRAFT,
        subject_reference=record.draft_id,
        tenant_scope_fingerprint=tenant_scope_fingerprint,
        source_scope_fingerprint=source_scope_fingerprint,
        draft_id=record.draft_id,
        draft_revision=record.resulting_revision,
        operator_audit_reference=record.operator_reference,
        payload_bindings=AuditPayloadBindings(),
        predecessor_event_ids=tuple(predecessor_event_ids),
        occurred_at=occurred_at or record.occurred_at,
    )


def lint_reference_audit_entry(
    *,
    tenant_scope_fingerprint: str,
    source_scope_fingerprint: str,
    event_id: str,
    draft_id: str,
    draft_revision: int | None,
    lint_reference: str,
    operator_audit_reference: str | None = None,
    predecessor_event_ids: Sequence[str] = (),
    occurred_at: datetime | None = None,
) -> AssemblyAuditEvidenceEntry:
    """Link a supplied lint summary reference into the draft's evidence trail.

    Lint readiness is recorded as read-only evidence; it never becomes a
    publication authority by being referenced here.
    """
    return AssemblyAuditEvidenceEntry(
        event_id=event_id,
        event_kind=AuditEventKind.LINT_REFERENCE,
        subject_kind=AuditSubjectKind.DRAFT,
        subject_reference=draft_id,
        tenant_scope_fingerprint=tenant_scope_fingerprint,
        source_scope_fingerprint=source_scope_fingerprint,
        draft_id=draft_id,
        draft_revision=draft_revision,
        operator_audit_reference=operator_audit_reference,
        payload_bindings=AuditPayloadBindings(lint_reference=lint_reference),
        predecessor_event_ids=tuple(predecessor_event_ids),
        occurred_at=occurred_at or _utc_now(),
    )


def verification_reference_audit_entry(
    *,
    tenant_scope_fingerprint: str,
    source_scope_fingerprint: str,
    event_id: str,
    draft_id: str,
    draft_revision: int | None,
    evidence_fingerprint: str,
    policy_profile: str | None = None,
    policy_version: int | None = None,
    plan_fingerprint: str | None = None,
    operator_audit_reference: str | None = None,
    predecessor_event_ids: Sequence[str] = (),
    occurred_at: datetime | None = None,
) -> AssemblyAuditEvidenceEntry:
    """Link bounded Verification Suite evidence into the draft's trail."""
    return AssemblyAuditEvidenceEntry(
        event_id=event_id,
        event_kind=AuditEventKind.VERIFICATION_REFERENCE,
        subject_kind=AuditSubjectKind.DRAFT,
        subject_reference=draft_id,
        tenant_scope_fingerprint=tenant_scope_fingerprint,
        source_scope_fingerprint=source_scope_fingerprint,
        draft_id=draft_id,
        draft_revision=draft_revision,
        operator_audit_reference=operator_audit_reference,
        payload_bindings=AuditPayloadBindings(
            evidence_fingerprint=evidence_fingerprint,
            policy_profile=policy_profile,
            policy_version=policy_version,
            plan_fingerprint=plan_fingerprint,
        ),
        predecessor_event_ids=tuple(predecessor_event_ids),
        occurred_at=occurred_at or _utc_now(),
    )


def publication_audit_entry(
    binding: PublicationAuditEvidence,
    *,
    predecessor_event_ids: Sequence[str] = (),
    occurred_at: datetime | None = None,
) -> AssemblyAuditEvidenceEntry:
    """Create the bounded publication entry for one publication binding."""
    return AssemblyAuditEvidenceEntry(
        event_id=binding.publication_event_id(),
        event_kind=AuditEventKind.PUBLICATION,
        subject_kind=AuditSubjectKind.PUBLICATION,
        subject_reference=binding.publish_audit_reference,
        tenant_scope_fingerprint=binding.tenant_scope_fingerprint,
        source_scope_fingerprint=binding.source_scope_fingerprint,
        draft_id=binding.approved_draft_id,
        draft_revision=binding.approved_draft_revision,
        bundle_fingerprint=binding.bundle_fingerprint,
        lifecycle_reference=binding.publish_audit_reference,
        payload_bindings=AuditPayloadBindings(
            plan_fingerprint=binding.approved_plan_fingerprint,
            manifest_fingerprint=binding.manifest_fingerprint,
            evidence_fingerprint=binding.verification_evidence_fingerprint,
            policy_profile=binding.policy_profile,
            policy_version=binding.policy_version,
            policy_fingerprint=binding.policy_fingerprint,
            lint_reference=binding.lint_reference,
            publish_audit_reference=binding.publish_audit_reference,
            separation_mode=binding.separation_mode,
            separation_allowed=binding.separation_allowed,
        ),
        predecessor_event_ids=tuple(predecessor_event_ids),
        occurred_at=occurred_at or _utc_now(),
    )


def activation_audit_entry(
    *,
    tenant_scope_fingerprint: str,
    source_scope_fingerprint: str,
    event_id: str,
    bundle_fingerprint: str,
    lifecycle_reference: str,
    resulting_active_fingerprint: str,
    prior_active_fingerprint: str | None = None,
    operator_audit_reference: str | None = None,
    predecessor_event_ids: Sequence[str] = (),
    occurred_at: datetime | None = None,
) -> AssemblyAuditEvidenceEntry:
    """Create the bounded activation entry for one pointer change.

    Activation evidence links to the immutable publication evidence for the
    activated Bundle through its predecessor publication event; it never
    republishes or mutates semantic content.
    """
    return AssemblyAuditEvidenceEntry(
        event_id=event_id,
        event_kind=AuditEventKind.ACTIVATION,
        subject_kind=AuditSubjectKind.ACTIVATION,
        subject_reference=lifecycle_reference,
        tenant_scope_fingerprint=tenant_scope_fingerprint,
        source_scope_fingerprint=source_scope_fingerprint,
        bundle_fingerprint=bundle_fingerprint,
        lifecycle_reference=lifecycle_reference,
        operator_audit_reference=operator_audit_reference,
        payload_bindings=AuditPayloadBindings(
            prior_active_fingerprint=prior_active_fingerprint,
            resulting_active_fingerprint=resulting_active_fingerprint,
        ),
        predecessor_event_ids=tuple(predecessor_event_ids),
        occurred_at=occurred_at or _utc_now(),
    )


def rollback_audit_entry(
    *,
    tenant_scope_fingerprint: str,
    source_scope_fingerprint: str,
    event_id: str,
    bundle_fingerprint: str,
    lifecycle_reference: str,
    prior_active_fingerprint: str,
    restored_fingerprint: str,
    operator_audit_reference: str | None = None,
    predecessor_event_ids: Sequence[str] = (),
    occurred_at: datetime | None = None,
) -> AssemblyAuditEvidenceEntry:
    """Create the bounded rollback entry keeping both versions explainable."""
    return AssemblyAuditEvidenceEntry(
        event_id=event_id,
        event_kind=AuditEventKind.ROLLBACK,
        subject_kind=AuditSubjectKind.ROLLBACK,
        subject_reference=lifecycle_reference,
        tenant_scope_fingerprint=tenant_scope_fingerprint,
        source_scope_fingerprint=source_scope_fingerprint,
        bundle_fingerprint=bundle_fingerprint,
        lifecycle_reference=lifecycle_reference,
        operator_audit_reference=operator_audit_reference,
        payload_bindings=AuditPayloadBindings(
            prior_active_fingerprint=prior_active_fingerprint,
            resulting_active_fingerprint=restored_fingerprint,
        ),
        predecessor_event_ids=tuple(predecessor_event_ids),
        occurred_at=occurred_at or _utc_now(),
    )
