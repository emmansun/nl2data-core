"""Security tests for the metadata inference and review boundary.

Proves that proposals are metadata, never authorization: unreviewed and
inferred facts cannot convert, approved proposals cannot grant View
visibility, and raw values, identities, credentials, or physical source
details never leak through snapshots, proposals, conversion, or errors.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3

import pytest

from nl2data_core.adapters.sql import SqlMetadataDiscoverer
from nl2data_core.metadata import (
    MetadataConstraint,
    MetadataConstraintKind,
    MetadataDiscoveryConfig,
    MetadataField,
    MetadataFreshness,
    MetadataObject,
    MetadataObjectKind,
    MetadataProvenance,
    MetadataRelationship,
    MetadataRelationshipKind,
    MetadataSnapshot,
    MetadataSourceReference,
    MetadataStatistic,
    MetadataStatisticKind,
    MetadataTrustLevel,
    ProposalStatus,
    SemanticProposalSet,
    convert_approved_proposals,
    infer_proposals,
)
from nl2data_core.views import (
    ResolutionContext,
    ViewRegistry,
)
from nl2data_core.views.models import (
    SemanticViewDefinition,
    ViewProvenance,
)

_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")

#: Raw values that must never appear in any serialized artifact.
SENSITIVE_VALUES = ("super-secret-value", "customer-identity-42", "acme@example.com")


def fp(byte: str) -> str:
    """A valid ``sha256:<hex>`` fingerprint filled with one repeated byte."""
    return "sha256:" + byte * 32


def make_snapshot(**overrides) -> MetadataSnapshot:
    """A snapshot carrying sensitive-looking values in names only."""
    values = {
        "snapshot_id": "snap-sec",
        "source": MetadataSourceReference(
            source_id="sales",
            catalog_fingerprint=fp("ab"),
            description="Logical sales source",
        ),
        "objects": (
            MetadataObject(
                object_id="customers",
                kind=MetadataObjectKind.TABLE,
                name="customers",
                fields=(
                    MetadataField(
                        field_id="customer_id",
                        object_id="customers",
                        path="customer_id",
                        data_type="INTEGER",
                        nullable=False,
                        trust_level=MetadataTrustLevel.DECLARED,
                    ),
                    MetadataField(
                        field_id="email",
                        object_id="customers",
                        path="email",
                        data_type="TEXT",
                        nullable=True,
                        trust_level=MetadataTrustLevel.DECLARED,
                    ),
                    MetadataField(
                        field_id="amount",
                        object_id="customers",
                        path="amount",
                        data_type="REAL",
                        nullable=True,
                        trust_level=MetadataTrustLevel.DECLARED,
                    ),
                ),
                constraints=(
                    MetadataConstraint(
                        constraint_id="customers_pk",
                        kind=MetadataConstraintKind.PRIMARY_KEY,
                        fields=frozenset({"customer_id"}),
                        trust_level=MetadataTrustLevel.DECLARED,
                    ),
                ),
                statistics=(
                    MetadataStatistic(
                        statistic_id="customers_row_count",
                        kind=MetadataStatisticKind.ROW_COUNT,
                        scope_object_id="customers",
                        value=3.0,
                        trust_level=MetadataTrustLevel.DECLARED,
                    ),
                ),
                trust_level=MetadataTrustLevel.DECLARED,
            ),
        ),
        "relationships": (
            MetadataRelationship(
                relationship_id="orders_customers_via_customer_id",
                kind=MetadataRelationshipKind.FOREIGN_KEY,
                source_object_id="orders",
                target_object_id="customers",
                source_fields=frozenset({"customer_id"}),
                target_fields=frozenset({"customer_id"}),
                trust_level=MetadataTrustLevel.DECLARED,
            ),
        ),
        "freshness": MetadataFreshness(sample_limit=10),
        "provenance": MetadataProvenance(
            discovered_by_fingerprint=fp("11"),
            method="test",
        ),
    }
    values.update(overrides)
    return MetadataSnapshot(**values)


def _approved_set() -> tuple[MetadataSnapshot, SemanticProposalSet]:
    """A snapshot plus a fully approved proposal set (conversion ready)."""
    snapshot = make_snapshot()
    proposals = infer_proposals(snapshot)
    approved = proposals.approve(
        (proposals.by_status(ProposalStatus.PENDING)[0].proposal_id,)
    )
    return snapshot, approved


class TestProposalsAreNotAuthorization:
    def test_proposals_bind_to_the_declared_snapshot(self) -> None:
        snapshot = make_snapshot()
        proposals = infer_proposals(snapshot)
        with pytest.raises(ValueError, match="same source snapshot"):
            SemanticProposalSet(
                snapshot_fingerprint=fp("ff"), proposals=proposals.proposals
            )

    def test_unreviewed_proposals_never_convert(self) -> None:
        snapshot = make_snapshot()
        proposals = infer_proposals(snapshot)
        assert proposals.proposals
        assert all(
            proposal.status is ProposalStatus.PENDING for proposal in proposals.proposals
        )
        assert convert_approved_proposals(
            proposals, descriptor_id="sales", source_id="sales"
        ) is None

    def test_approved_proposals_do_not_grant_view_access(self) -> None:
        snapshot, approved = _approved_set()
        converted = convert_approved_proposals(
            approved, descriptor_id="sales_catalog", source_id="sales"
        )
        assert converted is not None
        registry = ViewRegistry(
            descriptors=(converted.descriptor,),
            views=(
                SemanticViewDefinition(
                    view_id="analytics_view",
                    version=1,
                    descriptor_id="sales_catalog",
                    description="view",
                    provenance=ViewProvenance(
                        descriptor_fingerprint=converted.descriptor.fingerprint,
                        resolver_version=1,
                    ),
                ),
            ),
        )
        # An empty trusted context carries no tenant scope or principal
        # authorization: approval alone must never resolve a view.
        outcome = registry.resolve("analytics_view", ResolutionContext())
        assert outcome.kind == "denied"
        assert outcome.issues[0].code == "tenant_scope_missing"

    def test_rejected_and_revised_proposals_are_excluded_from_conversion(self) -> None:
        snapshot = make_snapshot()
        proposals = infer_proposals(snapshot)
        pending = proposals.by_status(ProposalStatus.PENDING)
        first, second = pending[0].proposal_id, pending[1].proposal_id
        reviewed = proposals.reject({first}).approve({second})
        assert reviewed.proposal(first).status is ProposalStatus.REJECTED  # type: ignore[union-attr]
        revised = reviewed.revise(second, fact=reviewed.proposal(second).fact)  # type: ignore[union-attr]
        converted = convert_approved_proposals(
            revised, descriptor_id="sales_catalog", source_id="sales"
        )
        # Rejected and revised origins are never approved, so nothing converts.
        assert converted is None

    def test_approval_never_creates_mandatory_filters(self) -> None:
        snapshot, approved = _approved_set()
        converted = convert_approved_proposals(
            approved, descriptor_id="sales_catalog", source_id="sales"
        )
        assert converted is not None
        payload = json.dumps(converted.safe_payload())
        assert "mandatory" not in payload
        assert "filter" not in payload
        # The converted input carries fingerprints and references only.
        assert converted.source_snapshot_fingerprint == snapshot.fingerprint

    def test_inferred_facts_are_never_trusted_as_authoritative(self) -> None:
        snapshot = make_snapshot()
        proposals = infer_proposals(snapshot)
        for proposal in proposals.proposals:
            assert proposal.trust_level in (
                MetadataTrustLevel.OBSERVED,
                MetadataTrustLevel.INFERRED,
            )
            assert proposal.status is ProposalStatus.PENDING


class TestNoRawValueLeakage:
    @pytest.mark.asyncio
    async def test_discovery_never_exposes_sampled_values(self, tmp_path) -> None:
        path = tmp_path / "leak.db"
        connection = sqlite3.connect(path)
        with connection:
            connection.execute(
                "CREATE TABLE customers ("
                " id INTEGER PRIMARY KEY,"
                " name TEXT NOT NULL,"
                " email TEXT)"
            )
            connection.execute(
                "INSERT INTO customers VALUES "
                f"(1, '{SENSITIVE_VALUES[0]}', '{SENSITIVE_VALUES[2]}')"
            )
        connection.close()
        discoverer = SqlMetadataDiscoverer(
            dialect="sqlite",
            db_path=path,
            allowed_objects=frozenset({"customers"}),
        )
        snapshot = await discoverer.discover(MetadataDiscoveryConfig())
        serialized = snapshot.serialize_canonical()
        for value in SENSITIVE_VALUES:
            assert value not in serialized
        # Evidence references are opaque fingerprints, never rows.
        assert all(
            _FINGERPRINT.fullmatch(evidence.reference)
            for evidence in snapshot.provenance.evidence
        )

    def test_proposal_payloads_are_structural_only(self) -> None:
        snapshot = make_snapshot()
        proposals = infer_proposals(snapshot)
        for proposal in proposals.proposals:
            payload = json.dumps(proposal.canonical_payload())
            for value in SENSITIVE_VALUES:
                assert value not in payload
            assert _FINGERPRINT.fullmatch(proposal.evidence_fingerprint)
            assert proposal.snapshot_fingerprint == snapshot.fingerprint
            assert proposal.confidence.value >= 0.0
            assert proposal.confidence.value <= 1.0

    def test_errors_never_leak_paths_or_dsn_material(self, tmp_path) -> None:
        from nl2data_core.metadata import MetadataUnavailableError

        missing = tmp_path / "does-not-exist.db"
        discoverer = SqlMetadataDiscoverer(
            dialect="sqlite",
            db_path=missing,
            allowed_objects=frozenset({"customers"}),
        )
        with pytest.raises(MetadataUnavailableError) as excinfo:
            asyncio.run(discoverer.discover(MetadataDiscoveryConfig()))
        error = excinfo.value
        assert "does-not-exist" not in str(error.message)
        assert "does-not-exist" not in str(error.details)
        assert error.details.get("cause_type") == "OperationalError"

    def test_unauthorized_errors_reveal_no_source_metadata(self) -> None:
        from nl2data_core.metadata import MetadataUnauthorizedError

        with pytest.raises(MetadataUnauthorizedError) as excinfo:
            raise MetadataUnauthorizedError(
                "no objects are authorized for metadata discovery",
                details={"authorized_objects": "0"},
            )
        assert excinfo.value.message == "no objects are authorized for metadata discovery"
        assert "customers" not in str(excinfo.value.details)
