"""Bounded pre-publication semantic assembly lifecycle models."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nl2data_core.bundles.models import BundleCompatibility, SemanticSourceReference
from nl2data_core.canonical import strict_canonical_json, strict_sha256_fingerprint
from nl2data_core.verification.models import VerificationPlan
from nl2data_core.views.models import validate_safe_description

ASSEMBLY_API_VERSION: Literal["nl2data.io/semantic-assembly/v1alpha1"] = (
    "nl2data.io/semantic-assembly/v1alpha1"
)

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_MAX_ASSERTIONS = 16_384
_MAX_DEPLOYMENT_BINDINGS = 64
_MAX_PAYLOAD_ITEMS = 16_384
_MAX_PAYLOAD_DEPTH = 32
_MAX_PAYLOAD_TEXT_CHARS = 4_096
_MAX_REFERENCE_CHARS = 256
_MAX_REASON_CHARS = 1_024


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DraftRevisionConflict(ValueError):
    """Raised when a draft mutation presents a stale expected revision."""

    def __init__(self, *, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"draft revision conflict: expected {expected}, current revision is {actual}"
        )


class AssemblyState(StrEnum):
    """Pre-publication and terminal assembly lifecycle states."""

    DRAFT = "draft"
    REVIEW = "review"
    APPROVED = "approved"
    PUBLISHED = "published"


class AssertionType(StrEnum):
    """Closed set of semantic facts governed as assertions."""

    ENTITY = "entity"
    FIELD = "field"
    RELATIONSHIP = "relationship"
    MAPPING = "mapping"
    POLICY = "policy"
    CALCULATED_FIELD = "calculated_field"
    MEASURE = "measure"
    GRAIN = "grain"


class ReviewState(StrEnum):
    """Review decision for one semantic assertion."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class AssertionProvenanceKind(StrEnum):
    """How semantic assertion content entered an assembly draft."""

    MANUAL = "manual"
    DISCOVERED = "discovered"
    INFERRED = "inferred"
    LLM_SUGGESTED = "llm-suggested"


class _FrozenPayload(dict[str, Any]):
    """Deeply immutable semantic assertion payload mapping."""

    def _raise_immutable(self) -> None:
        raise TypeError("semantic assertion payloads are immutable")

    def __setitem__(self, key: str, value: Any) -> None:
        self._raise_immutable()

    def __delitem__(self, key: str) -> None:
        self._raise_immutable()

    def __ior__(self, value: Any) -> _FrozenPayload:  # type: ignore[override,misc]
        self._raise_immutable()
        raise AssertionError("unreachable")

    def clear(self) -> None:
        self._raise_immutable()

    def pop(self, key: str, default: Any = None) -> Any:
        self._raise_immutable()
        raise AssertionError("unreachable")

    def popitem(self) -> tuple[str, Any]:
        self._raise_immutable()
        raise AssertionError("unreachable")

    def setdefault(self, key: str, default: Any = None) -> Any:
        self._raise_immutable()
        raise AssertionError("unreachable")

    def update(self, *args: Any, **kwargs: Any) -> None:
        self._raise_immutable()


def _freeze_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    item_count = 0

    def freeze(item: Any, *, depth: int) -> Any:
        nonlocal item_count
        item_count += 1
        if item_count > _MAX_PAYLOAD_ITEMS:
            raise ValueError(
                f"assertion payloads are limited to {_MAX_PAYLOAD_ITEMS} items"
            )
        if depth > _MAX_PAYLOAD_DEPTH:
            raise ValueError(
                f"assertion payload depth is limited to {_MAX_PAYLOAD_DEPTH}"
            )
        if isinstance(item, Mapping):
            frozen: dict[str, Any] = {}
            for key, member in item.items():
                if not isinstance(key, str) or not key or len(key) > 128:
                    raise ValueError("assertion payload keys must be bounded strings")
                frozen[key] = freeze(member, depth=depth + 1)
            return cast(dict[str, Any], _FrozenPayload(frozen))
        if isinstance(item, (list, tuple)):
            return tuple(freeze(member, depth=depth + 1) for member in item)
        if isinstance(item, str):
            if len(item) > _MAX_PAYLOAD_TEXT_CHARS:
                raise ValueError(
                    "assertion payload text is limited to "
                    f"{_MAX_PAYLOAD_TEXT_CHARS} characters"
                )
            return item
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("assertion payload numbers must be finite")
        if isinstance(item, (int, float, bool, type(None))):
            return item
        raise ValueError("assertion payloads must contain JSON-compatible values")

    return cast(dict[str, Any], freeze(value, depth=0))


def _thaw_payload(value: Any) -> Any:
    """Invert :func:`_freeze_payload` back to plain JSON containers.

    Canonical payloads must be JSON-safe: frozen mappings and tuples are
    model-native storage shapes, so fingerprint payloads carry them as
    plain dicts and JSON arrays.
    """
    if isinstance(value, Mapping):
        return {str(key): _thaw_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_payload(item) for item in value]
    return value


def _identity_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError(f"assertion identity member '{key}' must be a bounded string")
    return value


def _identity_strings(payload: Mapping[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError(f"assertion identity member '{key}' must be a string collection")
    members = [_identity_string({key: member}, key) for member in value]
    if len(members) != len(set(members)):
        raise ValueError(f"assertion identity member '{key}' must contain unique values")
    return sorted(members)


def assertion_identity_payload(
    assertion_type: AssertionType,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Extract the closed type-specific identity domain of an assertion."""
    descriptor_id = _identity_string(payload, "descriptor_id")
    if assertion_type is AssertionType.ENTITY:
        return {"descriptor_id": descriptor_id, "entity_id": _identity_string(payload, "entity_id")}
    if assertion_type in (AssertionType.FIELD, AssertionType.MAPPING):
        return {
            "descriptor_id": descriptor_id,
            "entity_id": _identity_string(payload, "entity_id"),
            "field_id": _identity_string(payload, "field_id"),
        }
    if assertion_type is AssertionType.RELATIONSHIP:
        return {
            "descriptor_id": descriptor_id,
            "relationship_id": _identity_string(payload, "relationship_id"),
            "source_entity_id": _identity_string(payload, "source_entity_id"),
            "target_entity_id": _identity_string(payload, "target_entity_id"),
            "source_fields": _identity_strings(payload, "source_fields"),
            "target_fields": _identity_strings(payload, "target_fields"),
        }
    if assertion_type is AssertionType.POLICY:
        return {"descriptor_id": descriptor_id, "policy_id": _identity_string(payload, "policy_id")}
    if assertion_type is AssertionType.CALCULATED_FIELD:
        return {
            "descriptor_id": descriptor_id,
            "entity_id": _identity_string(payload, "entity_id"),
            "name": _identity_string(payload, "name"),
        }
    identity_key = "measure_id" if assertion_type is AssertionType.MEASURE else "grain_id"
    return {"descriptor_id": descriptor_id, identity_key: _identity_string(payload, identity_key)}


def derive_assertion_id(
    assertion_type: AssertionType,
    payload: Mapping[str, Any],
) -> str:
    """Derive a stable assertion ID from identity semantics only."""
    return strict_sha256_fingerprint(
        {
            "assertion_type": assertion_type.value,
            "identity": assertion_identity_payload(assertion_type, payload),
        }
    )


class AssertionProvenance(BaseModel):
    """Safe audit-side origin metadata for one assertion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: AssertionProvenanceKind
    source_reference: str | None = Field(
        default=None, min_length=1, max_length=_MAX_REFERENCE_CHARS
    )
    proposal_reference: str | None = Field(
        default=None, pattern=_IDENTIFIER_PATTERN
    )
    snapshot_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    evidence_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    method: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("source_reference", "method")
    @classmethod
    def _safe_text(cls, value: str | None) -> str | None:
        return validate_safe_description(value) if value is not None else None


class ReviewBinding(BaseModel):
    """A bounded review decision bound to one assertion payload hash."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    payload_hash: str = Field(pattern=_FINGERPRINT_PATTERN)
    reviewer_reference: str = Field(min_length=1, max_length=_MAX_REFERENCE_CHARS)
    reviewed_at: datetime = Field(default_factory=_utc_now)
    reason: str = Field(default="", max_length=_MAX_REASON_CHARS)

    @field_validator("reviewer_reference", "reason")
    @classmethod
    def _safe_text(cls, value: str) -> str:
        return validate_safe_description(value)


class DeploymentBinding(BaseModel):
    """Safe deployment-specific connection reference outside semantic identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    binding_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    environment: str = Field(pattern=_IDENTIFIER_PATTERN)
    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    connection_reference: str = Field(min_length=1, max_length=_MAX_REFERENCE_CHARS)

    @field_validator("connection_reference")
    @classmethod
    def _safe_reference(cls, value: str) -> str:
        lowered = value.lower()
        if any(
            marker in lowered
            for marker in (
                "password=",
                "secret=",
                "token=",
                "api_key=",
                "://",
                "@",
            )
        ):
            raise ValueError("deployment bindings cannot contain inline credentials")
        scheme, separator, reference = value.partition(":")
        if separator != ":" or scheme not in {"env", "vault", "file"}:
            raise ValueError("deployment bindings require env:, vault:, or file: references")
        if not reference or any(character.isspace() for character in reference):
            raise ValueError("deployment binding references must be non-empty and whitespace-free")
        if scheme == "env" and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", reference) is None:
            raise ValueError("env deployment references must name one environment variable")
        if scheme in {"vault", "file"} and re.fullmatch(
            r"[A-Za-z0-9_./\\:\-]+",
            reference,
        ) is None:
            raise ValueError("deployment binding path contains unsupported characters")
        return value

    @property
    def reference_scheme(self) -> Literal["env", "vault", "file"]:
        return cast("Literal['env', 'vault', 'file']", self.connection_reference.split(":", 1)[0])


class SemanticAssertion(BaseModel):
    """One deterministic review unit in a semantic assembly draft."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=_FINGERPRINT_PATTERN)
    type: AssertionType
    payload: dict[str, Any]
    provenance: AssertionProvenance
    review_state: ReviewState = ReviewState.PENDING
    review_binding: ReviewBinding | None = None

    @field_validator("payload")
    @classmethod
    def _immutable_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("semantic assertions require a non-empty payload")
        return _freeze_payload(value)

    @model_validator(mode="after")
    def _id_matches_identity(self) -> SemanticAssertion:
        expected = derive_assertion_id(self.type, self.payload)
        if self.id != expected:
            raise ValueError("assertion id does not match its identity semantics")
        if self.review_state is ReviewState.PENDING:
            if self.review_binding is not None:
                raise ValueError("pending assertions must not carry a review binding")
        elif self.review_binding is None:
            raise ValueError("reviewed assertions require a review binding")
        elif self.review_binding.payload_hash != self.payload_hash():
            raise ValueError("review binding does not match the assertion payload")
        return self

    def canonical_payload(self) -> dict[str, Any]:
        """Return semantic assertion content without lifecycle metadata."""
        return {"type": self.type.value, "payload": _thaw_payload(self.payload)}

    def payload_hash(self) -> str:
        """Hash the canonical semantic content reviewed by a decision."""
        return strict_sha256_fingerprint(self.canonical_payload())

    def has_valid_review_binding(self) -> bool:
        """Whether a reviewed assertion remains bound to its current payload."""
        return (
            self.review_state is not ReviewState.PENDING
            and self.review_binding is not None
            and self.review_binding.payload_hash == self.payload_hash()
        )

    def replace_payload(self, payload: Mapping[str, Any]) -> SemanticAssertion:
        """Replace semantic content and invalidate any prior review decision."""
        return SemanticAssertion.create(
            type=self.type,
            payload=payload,
            provenance=self.provenance,
        )

    @classmethod
    def create(
        cls,
        *,
        type: AssertionType,
        payload: Mapping[str, Any],
        provenance: AssertionProvenance,
    ) -> SemanticAssertion:
        """Construct a pending assertion with its deterministic identity."""
        return cls(
            id=derive_assertion_id(type, payload),
            type=type,
            payload=dict(payload),
            provenance=provenance,
        )

    def bind_review(
        self,
        *,
        state: Literal[ReviewState.APPROVED, ReviewState.REJECTED],
        reviewer_reference: str,
        reason: str = "",
    ) -> SemanticAssertion:
        """Return this assertion with a review decision bound to its payload."""
        binding = ReviewBinding(
            payload_hash=self.payload_hash(),
            reviewer_reference=reviewer_reference,
            reason=reason,
        )
        return SemanticAssertion.model_validate(
            {
                **self.model_dump(mode="python"),
                "review_state": state,
                "review_binding": binding,
            }
        )


class AssemblyDraft(BaseModel):
    """Editable pre-publication assembly state with optimistic revision metadata."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
    )

    api_version: Literal["nl2data.io/semantic-assembly/v1alpha1"] = Field(
        alias="apiVersion"
    )
    draft_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    bundle_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    model_version: str = Field(pattern=_IDENTIFIER_PATTERN)
    state: AssemblyState = AssemblyState.DRAFT
    draft_revision: int = Field(default=0, ge=0, le=2**63 - 1)
    assertions: tuple[SemanticAssertion, ...] = Field(
        default_factory=tuple, max_length=_MAX_ASSERTIONS
    )
    deployment_bindings: tuple[DeploymentBinding, ...] = Field(
        default_factory=tuple, max_length=_MAX_DEPLOYMENT_BINDINGS
    )
    author_reference: str = Field(min_length=1, max_length=_MAX_REFERENCE_CHARS)
    review_submitted_by: str | None = Field(
        default=None,
        min_length=1,
        max_length=_MAX_REFERENCE_CHARS,
    )
    approved_by: str | None = Field(
        default=None,
        min_length=1,
        max_length=_MAX_REFERENCE_CHARS,
    )
    verification_plan: VerificationPlan | None = None
    approved_verification_plan_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    source_snapshot_fingerprint: str | None = Field(
        default=None, pattern=_FINGERPRINT_PATTERN
    )
    authoring_description: str | None = Field(
        default=None,
        max_length=1_024,
    )
    authoring_source_references: tuple[SemanticSourceReference, ...] | None = Field(
        default=None,
        max_length=64,
    )
    authoring_compatibility: BundleCompatibility | None = None

    @field_validator(
        "author_reference",
        "review_submitted_by",
        "approved_by",
        "authoring_description",
    )
    @classmethod
    def _safe_author_reference(cls, value: str | None) -> str | None:
        return validate_safe_description(value) if value is not None else None

    @field_validator("assertions")
    @classmethod
    def _unique_assertions(
        cls, value: tuple[SemanticAssertion, ...]
    ) -> tuple[SemanticAssertion, ...]:
        ids = [assertion.id for assertion in value]
        if len(ids) != len(set(ids)):
            raise ValueError("assertion ids must be unique within a draft")
        return value

    @field_validator("deployment_bindings")
    @classmethod
    def _unique_deployment_bindings(
        cls, value: tuple[DeploymentBinding, ...]
    ) -> tuple[DeploymentBinding, ...]:
        ids = [binding.binding_id for binding in value]
        if len(ids) != len(set(ids)):
            raise ValueError("deployment binding ids must be unique within a draft")
        return value

    @model_validator(mode="after")
    def _published_state_is_not_editable(self) -> AssemblyDraft:
        if self.state is AssemblyState.PUBLISHED:
            raise ValueError(
                "published semantic content must be represented as a SemanticModelBundle"
            )
        expected_plan_fingerprint = (
            self.verification_plan.fingerprint
            if self.state is AssemblyState.APPROVED and self.verification_plan is not None
            else None
        )
        if self.approved_verification_plan_fingerprint != expected_plan_fingerprint:
            raise ValueError(
                "approved verification plan binding must match the approved draft plan"
            )
        return self

    def file_payload(self) -> dict[str, Any]:
        """Return the deterministic, JSON-wire-safe assembly file payload."""
        payload = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        payload["assertions"] = sorted(payload["assertions"], key=lambda item: item["id"])
        payload["deployment_bindings"] = sorted(
            payload["deployment_bindings"], key=lambda item: item["binding_id"]
        )
        return payload

    def serialize_canonical(self) -> str:
        """Serialize this draft as deterministic canonical JSON."""
        return strict_canonical_json(self.file_payload())

    def require_revision(self, expected_revision: int) -> None:
        """Fail when a mutation was based on an older or future draft revision."""
        if expected_revision != self.draft_revision:
            raise DraftRevisionConflict(
                expected=expected_revision,
                actual=self.draft_revision,
            )

    def mutate(
        self,
        *,
        expected_revision: int,
        **changes: Any,
    ) -> AssemblyDraft:
        """Apply one validated immutable mutation and advance the revision."""
        self.require_revision(expected_revision)
        plan_changed = (
            "verification_plan" in changes
            and changes["verification_plan"] != self.verification_plan
        )
        if set(changes) == {"verification_plan"} and not plan_changed:
            return self
        if self.state is AssemblyState.APPROVED and changes and not (
            plan_changed and set(changes) == {"verification_plan"}
        ):
            raise ValueError(
                "approved draft content is frozen; return it to review before editing"
            )
        protected = {
            "api_version",
            "apiVersion",
            "draft_id",
            "draft_revision",
            "state",
            "approved_by",
            "approved_verification_plan_fingerprint",
        }
        attempted = protected.intersection(changes)
        if attempted:
            raise ValueError(
                "draft mutation cannot replace protected fields: "
                + ", ".join(sorted(attempted))
            )
        payload = {
            **self.model_dump(mode="python", by_alias=True),
            **changes,
            "draft_revision": self.draft_revision + 1,
        }
        if plan_changed:
            payload.update(
                {
                    "state": (
                        AssemblyState.REVIEW
                        if self.state is AssemblyState.APPROVED
                        else self.state
                    ),
                    "approved_by": None,
                    "approved_verification_plan_fingerprint": None,
                }
            )
        return AssemblyDraft.model_validate(payload)

    def transition(
        self,
        *,
        expected_revision: int,
        state: AssemblyState,
    ) -> AssemblyDraft:
        """Apply one legal lifecycle transition and advance the revision."""
        self.require_revision(expected_revision)
        allowed = {
            AssemblyState.DRAFT: frozenset({AssemblyState.REVIEW}),
            AssemblyState.REVIEW: frozenset(
                {AssemblyState.DRAFT, AssemblyState.APPROVED}
            ),
            AssemblyState.APPROVED: frozenset({AssemblyState.REVIEW}),
        }
        if state not in allowed.get(self.state, frozenset()):
            raise ValueError(
                f"invalid assembly transition from {self.state.value} to {state.value}"
            )
        if state is AssemblyState.APPROVED:
            pending = [
                assertion.id
                for assertion in self.assertions
                if assertion.review_state is ReviewState.PENDING
                or not assertion.has_valid_review_binding()
            ]
            if pending:
                raise ValueError("assembly approval requires all assertions to be reviewed")
        return AssemblyDraft.model_validate(
            {
                **self.model_dump(mode="python", by_alias=True),
                "state": state,
                "draft_revision": self.draft_revision + 1,
                "approved_by": (
                    self.approved_by if state is AssemblyState.APPROVED else None
                ),
                "approved_verification_plan_fingerprint": (
                    self.verification_plan.fingerprint
                    if state is AssemblyState.APPROVED
                    and self.verification_plan is not None
                    else None
                ),
            }
        )