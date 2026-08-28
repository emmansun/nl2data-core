"""Contract tests for resolver-stage value-semantics resolution.

Covers the mapping hit path (frozen IR with the stored value), VS_001
reject-policy misses, warn-policy warned outcomes, stored-value
pass-through, type-strict membership, per-value ``in`` resolution with
pre-freeze dedup, VS_002 operator restrictions, bundle-anchored
snapshot lookups with fail-closed unavailability, and the outcome
channel's separation from CompilationEvidence (designs D1, D8-D10).
"""

from __future__ import annotations

from tests.contract.test_intent_resolution import (
    REFERENCES,
    VIEW,
    request,
)
from tests.unit.test_semantic_model_bundles import fp, make_field

from nl2data_core.ai.errors import ModelErrorCode
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.ai.models import (
    RejectedIntent,
    ResolvedIntent,
    ValueResolutionOutcome,
)
from nl2data_core.ai.plan_builder import build_ir_from_intent
from nl2data_core.ai.resolver import IntentResolver
from nl2data_core.compilation.contract import CompilationEvidence
from nl2data_core.planning.ir.validation import validate_ir
from nl2data_core.views import (
    SemanticDescriptor,
    SemanticEntityDescriptor,
    ValueSemantics,
)

STATUS_MAPPING = {"paid": "PAID", "shipped": "SHIPPED"}


def str_snapshot(
    catalog_fingerprint: str | None = VIEW.catalog_fingerprint,
    **vs_overrides,
) -> SemanticDescriptor:
    """A descriptor snapshot whose ``status`` field maps str values."""
    status = make_field(
        "status",
        data_type="string",
        value_semantics=ValueSemantics(
            value_mapping=dict(STATUS_MAPPING), **vs_overrides
        ),
    )
    entity = SemanticEntityDescriptor(
        entity_id="order",
        label="Order",
        fields=(
            make_field("order_id", data_type="string"),
            make_field("amount"),
            status,
            make_field("created_at", data_type="datetime"),
        ),
    )
    return SemanticDescriptor(
        descriptor_id="sales_catalog",
        version=1,
        source_id="sales",
        catalog_fingerprint=catalog_fingerprint,
        entities=(entity,),
    )


def int_snapshot() -> SemanticDescriptor:
    """A descriptor snapshot whose ``status`` field maps int codes."""
    status = make_field(
        "status",
        data_type="string",
        value_semantics=ValueSemantics(
            value_mapping={"gold": 1, "silver": 2}
        ),
    )
    entity = SemanticEntityDescriptor(
        entity_id="order", label="Order", fields=(status,)
    )
    return SemanticDescriptor(
        descriptor_id="sales_catalog",
        version=1,
        source_id="sales",
        catalog_fingerprint=VIEW.catalog_fingerprint,
        entities=(entity,),
    )


_DEFAULT_SNAPSHOT = str_snapshot()


def vs_resolver(
    snapshot: SemanticDescriptor | None = _DEFAULT_SNAPSHOT,
    *,
    anchored: bool = True,
) -> IntentResolver:
    return IntentResolver(
        view=VIEW,
        semantic_references=REFERENCES,
        min_confidence=0.6,
        value_semantics_snapshot=snapshot,
        value_semantics_anchored=anchored,
    )


def intent_content(value, operator: str = "eq", field_id: str = "status"):
    return {
        "intent": {
            "source_id": "sales",
            "root_entity_id": "order",
            "selections": [
                {"selection_id": "s1", "field_id": "amount", "aggregation": "sum"},
            ],
            "filters": [
                {
                    "filter_id": "f1",
                    "field_id": field_id,
                    "operator": operator,
                    "value": value,
                }
            ],
            "limit": 10,
            "confidence": 0.9,
        }
    }


_NO_SNAPSHOT = object()


async def resolve(content, snapshot=_NO_SNAPSHOT, anchored=True):
    resolver = vs_resolver(
        str_snapshot() if snapshot is _NO_SNAPSHOT else snapshot,
        anchored=anchored,
    )
    return await resolver.resolve(
        request(), FakeModelProvider(default_response=content)
    )


def single_status(outcome: ValueResolutionOutcome) -> str:
    assert len(outcome.filters) == 1
    assert len(outcome.filters[0].values) == 1
    return outcome.filters[0].values[0].status


class TestMappingHit:
    async def test_hit_resolves_to_the_stored_value(self) -> None:
        outcome = await resolve(intent_content("paid"))
        assert isinstance(outcome, ResolvedIntent)
        assert outcome.intent.filters[0].value == "PAID"
        assert single_status(outcome.value_resolution) == "hit"

    async def test_hit_produces_a_frozen_ir_with_the_stored_value(self) -> None:
        outcome = await resolve(intent_content("paid"))
        assert isinstance(outcome, ResolvedIntent)
        ir = build_ir_from_intent(
            outcome.intent, catalog_fingerprint=VIEW.catalog_fingerprint
        )
        assert ir.filters[0].value == "PAID"
        assert validate_ir(ir, view=VIEW).valid is True

    async def test_ir_fingerprint_is_construction_path_independent(
        self,
    ) -> None:
        """["paid","PAID"] and ["PAID","paid"] freeze identically."""
        first = await resolve(intent_content(["paid", "PAID"], operator="in"))
        second = await resolve(intent_content(["PAID", "paid"], operator="in"))
        assert isinstance(first, ResolvedIntent) and isinstance(second, ResolvedIntent)
        assert first.intent.fingerprint == second.intent.fingerprint

    async def test_outcome_carries_the_anchored_snapshot_fingerprint(
        self,
    ) -> None:
        snapshot = str_snapshot()
        outcome = await resolve(intent_content("paid"), snapshot=snapshot)
        assert isinstance(outcome, ResolvedIntent)
        assert (
            outcome.value_resolution.snapshot_fingerprint
            == snapshot.fingerprint
        )
        assert outcome.value_resolution.snapshot_fingerprint.startswith("sha256:")


class TestUnknownValuePolicy:
    async def test_reject_policy_miss_produces_vs_001_and_no_ir(self) -> None:
        outcome = await resolve(intent_content("cancelled"))
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.VALUE_UNKNOWN
        details = outcome.error.safe_dump()["details"]
        assert details["field"] == "status"
        assert details["attempted_value"] == "cancelled"
        assert details["known_business_terms"] == "paid,shipped"

    async def test_reject_miss_still_records_the_miss_outcome(self) -> None:
        """The failure path keeps the outcome channel complete (D9)."""
        outcome = await resolve(intent_content("cancelled"))
        assert isinstance(outcome, RejectedIntent)
        assert outcome.value_resolution is not None
        assert single_status(outcome.value_resolution) == "miss"
        assert outcome.value_resolution.snapshot_fingerprint is not None

    async def test_warn_policy_miss_proceeds_with_a_warned_outcome(
        self,
    ) -> None:
        snapshot = str_snapshot(unknown_value_policy="warn")
        outcome = await resolve(intent_content("cancelled"), snapshot=snapshot)
        assert isinstance(outcome, ResolvedIntent)
        assert outcome.intent.filters[0].value == "cancelled"
        assert single_status(outcome.value_resolution) == "warned"


class TestControlledPassThrough:
    async def test_stored_value_passes_through(self) -> None:
        outcome = await resolve(intent_content("SHIPPED"))
        assert isinstance(outcome, ResolvedIntent)
        assert outcome.intent.filters[0].value == "SHIPPED"
        assert single_status(outcome.value_resolution) == "pass_through"


class TestTypeStrictMembership:
    async def test_int_domain_canonicalizes_a_canonical_wire_string(
        self,
    ) -> None:
        outcome = await resolve(
            intent_content("2"), snapshot=int_snapshot()
        )
        assert isinstance(outcome, ResolvedIntent)
        assert outcome.intent.filters[0].value == 2
        assert single_status(outcome.value_resolution) == "pass_through"

    async def test_int_domain_rejects_a_non_canonical_wire_string(
        self,
    ) -> None:
        outcome = await resolve(
            intent_content("02"), snapshot=int_snapshot()
        )
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.VALUE_UNKNOWN

    async def test_str_domain_never_accepts_an_int(self) -> None:
        outcome = await resolve(intent_content(42))
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.VALUE_UNKNOWN

    async def test_bool_is_never_coerced_into_an_int_domain(self) -> None:
        outcome = await resolve(intent_content(True), snapshot=int_snapshot())
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.VALUE_UNKNOWN


class TestInListResolution:
    async def test_mixed_list_resolves_per_value_with_dedup(self) -> None:
        outcome = await resolve(
            intent_content(["paid", "PAID", "paid"], operator="in")
        )
        assert isinstance(outcome, ResolvedIntent)
        assert outcome.intent.filters[0].value == ("PAID",)
        statuses = [
            value.status
            for value in outcome.value_resolution.filters[0].values
        ]
        assert statuses == ["hit", "pass_through", "hit"]

    async def test_per_value_miss_under_reject_fails_the_whole_filter(
        self,
    ) -> None:
        outcome = await resolve(
            intent_content(["paid", "cancelled"], operator="in")
        )
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.VALUE_UNKNOWN

    async def test_partial_outcomes_survive_a_mid_filter_miss(self) -> None:
        """Earlier hits in a mixed list keep their outcomes on failure."""
        outcome = await resolve(
            intent_content(["paid", "cancelled"], operator="in")
        )
        assert isinstance(outcome, RejectedIntent)
        assert outcome.value_resolution is not None
        statuses = [
            value.status
            for value in outcome.value_resolution.filters[0].values
        ]
        assert statuses == ["hit", "miss"]
        assert outcome.value_resolution.snapshot_fingerprint is not None


class TestOperatorWhitelist:
    async def test_disallowed_operator_produces_vs_002(self) -> None:
        outcome = await resolve(
            intent_content("PAID", operator="contains")
        )
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.VALUE_OPERATOR_DISALLOWED
        details = outcome.error.safe_dump()["details"]
        assert details["field"] == "status"
        assert details["attempted_operator"] == "contains"
        assert details["allowed_operators"] == "eq,in"

    async def test_vs_002_still_records_the_failing_filter(self) -> None:
        outcome = await resolve(
            intent_content("PAID", operator="contains")
        )
        assert isinstance(outcome, RejectedIntent)
        assert outcome.value_resolution is not None
        assert len(outcome.value_resolution.filters) == 1
        assert outcome.value_resolution.filters[0].values == ()
        assert outcome.value_resolution.snapshot_fingerprint is not None

    async def test_unmapped_fields_keep_every_operator(self) -> None:
        content = intent_content("alice", operator="contains", field_id="order_id")
        outcome = await resolve(content)
        assert isinstance(outcome, ResolvedIntent)
        assert outcome.intent.filters[0].value == "alice"
        assert single_status(outcome.value_resolution) == "unpolicied"


class TestSnapshotAnchoring:
    async def test_missing_snapshot_fails_closed(self) -> None:
        outcome = await resolve(intent_content("paid"), snapshot=None)
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.VALUE_SNAPSHOT_UNAVAILABLE

    async def test_stale_snapshot_fingerprint_fails_closed(self) -> None:
        stale = str_snapshot(catalog_fingerprint=fp("ff"))
        outcome = await resolve(intent_content("paid"), snapshot=stale)
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.VALUE_SNAPSHOT_UNAVAILABLE

    async def test_lookup_reads_the_anchored_snapshot_only(self) -> None:
        """Only the anchored snapshot's mapping governs resolution."""
        outcome = await resolve(intent_content("paid"))
        assert isinstance(outcome, ResolvedIntent)
        assert outcome.intent.filters[0].value == "PAID"
        # A value absent from the anchored snapshot is a miss even though
        # a stale registry could claim otherwise.
        other = await resolve(intent_content("archived"))
        assert isinstance(other, RejectedIntent)
        assert other.error.code == ModelErrorCode.VALUE_UNKNOWN


class TestLegacyUnanchoredPath:
    async def test_unanchored_resolver_leaves_filters_untouched(self) -> None:
        outcome = await resolve(intent_content("shipped"), anchored=False)
        assert isinstance(outcome, ResolvedIntent)
        assert outcome.intent.filters[0].value == "shipped"
        assert outcome.value_resolution is None


class TestPreResolutionValidation:
    async def test_non_scalar_values_fail_structured_validation_first(
        self,
    ) -> None:
        content = intent_content({"nested": "map"})
        outcome = await resolve(content)
        assert isinstance(outcome, RejectedIntent)
        assert outcome.error.code == ModelErrorCode.MALFORMED_RESPONSE


class TestOutcomeChannel:
    async def test_outcomes_aggregate_per_filter_occurrence(self) -> None:
        content = {
            "intent": {
                "source_id": "sales",
                "root_entity_id": "order",
                "selections": [
                    {
                        "selection_id": "s1",
                        "field_id": "amount",
                        "aggregation": "sum",
                    },
                ],
                "filters": [
                    {
                        "filter_id": "f1",
                        "field_id": "status",
                        "operator": "in",
                        "value": ["paid", "SHIPPED"],
                    },
                    {
                        "filter_id": "f2",
                        "field_id": "order_id",
                        "operator": "eq",
                        "value": "o-1",
                    },
                ],
                "limit": 10,
                "confidence": 0.9,
            }
        }
        outcome = await resolve(content)
        assert isinstance(outcome, ResolvedIntent)
        resolution = outcome.value_resolution
        assert [f.filter_id for f in resolution.filters] == ["f1", "f2"]
        assert resolution.status_count("hit") == 1
        assert resolution.status_count("pass_through") == 1
        assert resolution.status_count("unpolicied") == 1
        assert resolution.status_count("miss") == 0
        assert resolution.status_count("warned") == 0

    async def test_outcomes_never_enter_compilation_evidence(self) -> None:
        """Compilation evidence stays fingerprints-only (design D9)."""
        assert "value_resolution" not in CompilationEvidence.model_fields
        evidence_fields = set(CompilationEvidence.model_fields)
        assert not any("resolution" in name for name in evidence_fields)
