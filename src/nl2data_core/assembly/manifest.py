"""Immutable accepted-assertion manifests linked to published fingerprints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import (
    AssemblyDraft,
    AssemblyState,
    AssertionProvenance,
    AssertionProvenanceKind,
    AssertionType,
    ReviewState,
    SemanticAssertion,
    _freeze_payload,
)

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_MAX_ASSERTIONS = 16_384


class AcceptedAssertion(BaseModel):
    """One approved semantic assertion without lifecycle or provenance metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=_FINGERPRINT_PATTERN)
    type: AssertionType
    payload: dict[str, Any]
    payload_hash: str = Field(pattern=_FINGERPRINT_PATTERN)

    @field_validator("payload")
    @classmethod
    def _immutable_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _freeze_payload(value)

    @model_validator(mode="after")
    def _matches_semantic_content(self) -> AcceptedAssertion:
        assertion = SemanticAssertion.create(
            type=self.type,
            payload=self.payload,
            provenance=_MANIFEST_PROVENANCE,
        )
        if self.id != assertion.id:
            raise ValueError("manifest assertion id does not match identity semantics")
        if self.payload_hash != assertion.payload_hash():
            raise ValueError("manifest payload hash does not match semantic content")
        return self

    @classmethod
    def from_assertion(cls, assertion: SemanticAssertion) -> AcceptedAssertion:
        if assertion.review_state is not ReviewState.APPROVED:
            raise ValueError("accepted manifests contain approved assertions only")
        if not assertion.has_valid_review_binding():
            raise ValueError("accepted manifests require valid review bindings")
        return cls(
            id=assertion.id,
            type=assertion.type,
            payload=dict(assertion.payload),
            payload_hash=assertion.payload_hash(),
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "payload": dict(self.payload),
            "payload_hash": self.payload_hash,
        }


class AcceptedAssertionManifest(BaseModel):
    """Immutable control-plane index for one published semantic fingerprint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bundle_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    bundle_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    assertions: tuple[AcceptedAssertion, ...] = Field(
        default_factory=tuple,
        max_length=_MAX_ASSERTIONS,
    )

    @field_validator("assertions")
    @classmethod
    def _unique_assertions(
        cls,
        value: tuple[AcceptedAssertion, ...],
    ) -> tuple[AcceptedAssertion, ...]:
        ids = [assertion.id for assertion in value]
        if len(ids) != len(set(ids)):
            raise ValueError("accepted manifest assertion ids must be unique")
        return value

    @classmethod
    def from_draft(
        cls,
        draft: AssemblyDraft,
        *,
        bundle_fingerprint: str,
    ) -> AcceptedAssertionManifest:
        if draft.state is not AssemblyState.APPROVED:
            raise ValueError("accepted manifests require an approved assembly draft")
        if any(assertion.review_state is ReviewState.PENDING for assertion in draft.assertions):
            raise ValueError("accepted manifests cannot be derived from pending assertions")
        accepted = tuple(
            sorted(
                (
                    AcceptedAssertion.from_assertion(assertion)
                    for assertion in draft.assertions
                    if assertion.review_state is ReviewState.APPROVED
                ),
                key=lambda assertion: assertion.id,
            )
        )
        return cls(
            bundle_id=draft.bundle_id,
            bundle_fingerprint=bundle_fingerprint,
            assertions=accepted,
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "bundle_fingerprint": self.bundle_fingerprint,
            "assertions": [
                assertion.canonical_payload()
                for assertion in sorted(self.assertions, key=lambda item: item.id)
            ],
        }


_MANIFEST_PROVENANCE = AssertionProvenance(kind=AssertionProvenanceKind.MANUAL)