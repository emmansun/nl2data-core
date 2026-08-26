"""Bounded semantic proposals and their review workflow.

Discovery produces proposals, not active models.  Every proposal carries
its method, evidence fingerprint, confidence, trust level, freshness, and
source snapshot identity, and remains non-authoritative until a reviewer
explicitly approves it.  Proposals can never grant View visibility, tenant
access, mandatory filters, or execution authorization on their own - only
the View/governance resolution boundary does that.

Review operations are immutable: approving, rejecting, or revising a
proposal produces a new :class:`SemanticProposalSet`; the previous set is
never mutated.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nl2data_core.canonical import sha256_fingerprint

from .models import MetadataConfidence, MetadataTrustLevel

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

#: Bounded collection and text limits for proposal sets.
_MAX_PROPOSALS = 16_384
_MAX_FACT_ITEMS = 64
_MAX_FACT_TEXT_CHARS = 1_024
_MAX_METHOD_CHARS = 128


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SemanticProposalKind(StrEnum):
    """Kinds of semantic facts a proposal may introduce."""

    ENTITY = "entity"
    FIELD = "field"
    RELATIONSHIP = "relationship"
    GRAIN = "grain"
    MEASURE = "measure"
    ALIAS = "alias"
    CLASSIFICATION = "classification"
    IDENTIFIER = "identifier"


class ProposalStatus(StrEnum):
    """Review lifecycle of one proposal.

    ``PENDING`` proposals are inactive; ``APPROVED`` proposals are eligible
    for conversion into Semantic Model Bundle inputs; ``REJECTED`` proposals
    are excluded; ``REVISED`` proposals were superseded by a newer revision.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVISED = "revised"


class _FrozenFact(dict[str, Any]):
    """Deeply immutable proposal fact mapping."""

    def __init__(self, values: Mapping[str, Any]) -> None:
        super().__init__(values)

    def _raise_immutable(self) -> None:
        raise TypeError("proposal facts are immutable")

    def __setitem__(self, key: str, value: Any) -> None:
        self._raise_immutable()

    def __delitem__(self, key: str) -> None:
        self._raise_immutable()

    def __ior__(self, value: Any) -> _FrozenFact:  # type: ignore[override, misc]
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


def _freeze_fact(value: Mapping[str, Any]) -> dict[str, Any]:
    """Deeply freeze a bounded JSON-compatible fact mapping."""
    frozen: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or len(key) > 128:
            raise ValueError("proposal fact keys must be bounded strings")
        if isinstance(item, Mapping):
            frozen[key] = _freeze_fact(item)
        elif isinstance(item, (set, frozenset)):
            frozen[key] = tuple(sorted(str(member) for member in item))
        elif isinstance(item, (list, tuple)):
            frozen[key] = tuple(str(member) for member in item)
        elif isinstance(item, (str, int, float, bool, type(None))):
            if isinstance(item, str) and len(item) > _MAX_FACT_TEXT_CHARS:
                raise ValueError(
                    f"proposal fact text is limited to {_MAX_FACT_TEXT_CHARS} characters"
                )
            frozen[key] = item
        else:
            raise ValueError("proposal facts must be scalar, JSON-compatible values")
    if len(frozen) > _MAX_FACT_ITEMS:
        raise ValueError(f"proposal facts are limited to {_MAX_FACT_ITEMS} items")
    return cast(dict[str, Any], _FrozenFact(frozen))


class SemanticProposal(BaseModel):
    """One bounded semantic proposal with full trust/provenance metadata.

    ``fact`` is the typed payload of the proposal (entity/field/relationship
    references and bounded labels); ``evidence_fingerprint`` and
    ``snapshot_fingerprint`` tie the proposal to the discovery evidence and
    source snapshot it was generated from, and ``status`` records the review
    lifecycle.  An inferred or unreviewed proposal is metadata, never
    authorization.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    kind: SemanticProposalKind
    target_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    fact: dict[str, Any] = Field(default_factory=dict, max_length=_MAX_FACT_ITEMS)
    trust_level: MetadataTrustLevel = MetadataTrustLevel.INFERRED
    method: str = Field(min_length=1, max_length=_MAX_METHOD_CHARS)
    confidence: MetadataConfidence
    evidence_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    snapshot_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    freshness: datetime = Field(default_factory=_utc_now)
    status: ProposalStatus = ProposalStatus.PENDING
    revised_from: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)

    @field_validator("fact", mode="after")
    @classmethod
    def _freeze_fact_value(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _freeze_fact(value)

    @model_validator(mode="after")
    def _revision_consistency(self) -> SemanticProposal:
        if self.status is ProposalStatus.REVISED and self.revised_from is None:
            raise ValueError("revised proposals must record their origin proposal")
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "kind": self.kind.value,
            "target_id": self.target_id,
            "fact": dict(self.fact),
            "trust_level": self.trust_level.value,
            "method": self.method,
            "confidence": self.confidence.canonical_payload(),
            "evidence_fingerprint": self.evidence_fingerprint,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "freshness": self.freshness.isoformat(),
            "status": self.status.value,
            "revised_from": self.revised_from,
        }

    def evidence_fingerprint_of(self) -> str:
        """The stable fingerprint of this proposal's own canonical form.

        Used to reference the proposal from Bundle provenance and trust
        markers without exposing its payload.
        """
        return sha256_fingerprint(self.canonical_payload())


class SemanticProposalSet(BaseModel):
    """A bounded immutable set of proposals for one source snapshot.

    Review operations return new sets: :meth:`approve` marks the selected
    proposals approved (eligible for Bundle input conversion),
    :meth:`reject` excludes them, and :meth:`revise` supersedes a proposal
    with a new PENDING revision.  Unknown proposal ids raise ``ValueError``
    so review never silently skips a target.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    proposals: tuple[SemanticProposal, ...] = Field(
        default_factory=tuple, max_length=_MAX_PROPOSALS
    )
    reviewed_at: datetime | None = Field(default=None)

    @field_validator("proposals")
    @classmethod
    def _valid_proposals(
        cls, value: tuple[SemanticProposal, ...]
    ) -> tuple[SemanticProposal, ...]:
        ids = [proposal.proposal_id for proposal in value]
        if len(ids) != len(set(ids)):
            raise ValueError("proposal ids must be unique within a set")
        return value

    @model_validator(mode="after")
    def _snapshot_consistency(self) -> SemanticProposalSet:
        for proposal in self.proposals:
            if proposal.snapshot_fingerprint != self.snapshot_fingerprint:
                raise ValueError(
                    "all proposals in a set must reference the same source snapshot"
                )
        return self

    def canonical_payload(self) -> dict[str, object]:
        """The canonical payload used for set identity."""
        return {
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "proposals": [
                proposal.canonical_payload()
                for proposal in sorted(
                    self.proposals, key=lambda p: p.proposal_id
                )
            ],
        }

    def evidence_fingerprint_of(self) -> str:
        """Stable fingerprint of this proposal set's canonical form."""
        from nl2data_core.canonical import sha256_fingerprint

        return sha256_fingerprint(self.canonical_payload())

    def proposal(self, proposal_id: str) -> SemanticProposal | None:
        """The proposal with the given id, or ``None`` when absent."""
        for proposal in self.proposals:
            if proposal.proposal_id == proposal_id:
                return proposal
        return None

    def by_status(self, status: ProposalStatus) -> tuple[SemanticProposal, ...]:
        """Every proposal currently in the given review status."""
        return tuple(
            proposal for proposal in self.proposals if proposal.status is status
        )

    def _updated(
        self, proposals: Iterable[SemanticProposal], *, reviewed: bool = False
    ) -> SemanticProposalSet:
        return SemanticProposalSet(
            snapshot_fingerprint=self.snapshot_fingerprint,
            proposals=tuple(proposals),
            reviewed_at=_utc_now() if reviewed else self.reviewed_at,
        )

    def _select(self, proposal_ids: Iterable[str]) -> list[SemanticProposal]:
        if isinstance(proposal_ids, str):
            raise ValueError(
                "review requires proposal ids as an iterable, not a single string"
            )
        ids = set(proposal_ids)
        if not ids:
            raise ValueError("review requires at least one proposal id")
        unknown = ids - {proposal.proposal_id for proposal in self.proposals}
        if unknown:
            raise ValueError(f"unknown proposal ids: {', '.join(sorted(unknown))}")
        return [proposal for proposal in self.proposals if proposal.proposal_id in ids]

    def approve(self, proposal_ids: Iterable[str]) -> SemanticProposalSet:
        """Approve the selected proposals, producing a new reviewed set.

        Only approved proposals are eligible for conversion into Semantic
        Model Bundle inputs; approval never grants authorization by itself.
        """
        selected = {proposal.proposal_id for proposal in self._select(proposal_ids)}
        updated = [
            proposal.model_copy(
                update={"status": ProposalStatus.APPROVED}
            )
            if proposal.proposal_id in selected
            else proposal
            for proposal in self.proposals
        ]
        return self._updated(updated, reviewed=True)

    def reject(self, proposal_ids: Iterable[str]) -> SemanticProposalSet:
        """Reject the selected proposals, producing a new reviewed set."""
        selected = {proposal.proposal_id for proposal in self._select(proposal_ids)}
        updated = [
            proposal.model_copy(update={"status": ProposalStatus.REJECTED})
            if proposal.proposal_id in selected
            else proposal
            for proposal in self.proposals
        ]
        return self._updated(updated, reviewed=True)

    def revise(
        self, proposal_id: str, *, fact: Mapping[str, Any]
    ) -> SemanticProposalSet:
        """Supersede one proposal with a new PENDING revision.

        The origin proposal is marked ``revised`` and the new proposal
        carries a fresh id, the supplied fact, and an origin reference;
        the revision must be approved explicitly before conversion.
        """
        origin = self.proposal(proposal_id)
        if origin is None:
            raise ValueError(f"unknown proposal id: {proposal_id}")
        revision_count = sum(
            1 for p in self.proposals if p.revised_from == proposal_id
        )
        new_id = f"{proposal_id}-r{revision_count + 1}"
        revision = origin.model_copy(
            update={
                "proposal_id": new_id,
                "fact": _freeze_fact(fact),
                "status": ProposalStatus.PENDING,
                "revised_from": proposal_id,
                "freshness": _utc_now(),
            }
        )
        updated = [
            origin.model_copy(
                update={
                    "status": ProposalStatus.REVISED,
                    # A superseded origin records itself as the chain root
                    # so every revised proposal stays traceable.
                    "revised_from": proposal_id,
                }
            )
            if proposal.proposal_id == proposal_id
            else proposal
            for proposal in self.proposals
        ]
        return self._updated((*updated, revision), reviewed=True)
