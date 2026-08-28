"""Security tests for value-semantics resolution boundaries.

Resolution errors expose only the attempted business value and the
known business terms - never physical names, stored (mapped) values,
or the full mapping contents.  The outcome channel records bounded
statuses only, following the evidence redaction conventions (design
D9 and the safe-content rules of the error boundary).
"""

from __future__ import annotations

import json

from tests.contract.test_intent_resolution import (
    VIEW,
    request,
)
from tests.contract.test_value_semantics_resolution import (
    intent_content,
    str_snapshot,
    vs_resolver,
)

from nl2data_core.ai.errors import ModelErrorCode
from nl2data_core.ai.fake import FakeModelProvider
from nl2data_core.ai.models import RejectedIntent, ResolvedIntent, ValueResolutionOutcome
from nl2data_core.ai.value_semantics import (
    snapshot_unavailable_error,
    vs_001_error,
    vs_002_error,
)


class TestVS001ErrorSurface:
    def test_details_expose_only_attempted_value_and_known_terms(self) -> None:
        record = vs_001_error(
            field_id="status",
            attempted_value="canceled",
            known_business_terms=("paid", "shipped"),
        )
        payload = record.safe_dump()
        details = payload["details"]
        assert set(details) == {
            "field",
            "attempted_value",
            "known_business_terms",
        }
        assert details["attempted_value"] == "canceled"
        assert details["known_business_terms"] == "paid,shipped"

    def test_stored_values_never_leak_into_the_error(self) -> None:
        record = vs_001_error(
            field_id="status",
            attempted_value="canceled",
            known_business_terms=("paid", "shipped"),
        )
        dumped = json.dumps(record.safe_dump())
        assert "PAID" not in dumped
        assert "SHIPPED" not in dumped

    def test_known_terms_are_bounded(self) -> None:
        many_terms = tuple(f"term_{i:03d}" for i in range(64))
        record = vs_001_error(
            field_id="status",
            attempted_value="canceled",
            known_business_terms=many_terms,
        )
        terms = record.safe_dump()["details"]["known_business_terms"]
        assert len(terms.split(",")) == 32
        assert terms.split(",") == sorted(terms.split(","))


class TestVS002ErrorSurface:
    def test_details_expose_only_operator_information(self) -> None:
        record = vs_002_error(field_id="status", attempted_operator="gt")
        details = record.safe_dump()["details"]
        assert set(details) == {
            "field",
            "attempted_operator",
            "allowed_operators",
        }
        assert details["allowed_operators"] == "eq,in"

    def test_no_mapping_contents_in_vs002(self) -> None:
        record = vs_002_error(field_id="status", attempted_operator="ne")
        assert "PAID" not in json.dumps(record.safe_dump())


class TestSnapshotErrorSurface:
    def test_unavailable_error_carries_no_snapshot_identity(self) -> None:
        record = snapshot_unavailable_error()
        payload = record.safe_dump()
        assert payload["code"] == ModelErrorCode.VALUE_SNAPSHOT_UNAVAILABLE
        assert payload["details"] == {}


class TestEndToEndErrorRedaction:
    async def test_vs001_through_resolver_leaks_no_stored_values(self) -> None:
        resolver = vs_resolver()
        outcome = await resolver.resolve(
            request(), FakeModelProvider(default_response=intent_content("void"))
        )
        assert isinstance(outcome, RejectedIntent)
        dumped = json.dumps(outcome.error.safe_dump())
        assert "PAID" not in dumped
        assert "SHIPPED" not in dumped
        assert "void" in dumped  # the attempted business value is exposed
        # no physical column or table names in the error surface
        for physical in ("orders", "order_status", "sales.orders"):
            assert physical not in dumped

    async def test_vs002_through_resolver_leaks_no_mapping(self) -> None:
        resolver = vs_resolver()
        outcome = await resolver.resolve(
            request(),
            FakeModelProvider(
                default_response=intent_content("paid", operator="gt")
            ),
        )
        assert isinstance(outcome, RejectedIntent)
        assert "PAID" not in json.dumps(outcome.error.safe_dump())


class TestOutcomeChannelRedaction:
    async def test_outcome_records_statuses_only(self) -> None:
        resolver = vs_resolver()
        outcome = await resolver.resolve(
            request(), FakeModelProvider(default_response=intent_content("paid"))
        )
        assert isinstance(outcome, ResolvedIntent)
        dumped = json.dumps(outcome.value_resolution.model_dump())
        # raw filter values (business words and stored values) are absent
        assert "paid" not in dumped
        assert "PAID" not in dumped
        assert '"status": "hit"' in dumped

    async def test_warned_outcome_is_evidence_safe(self) -> None:
        snapshot = str_snapshot(unknown_value_policy="warn")
        resolver = vs_resolver(snapshot)
        outcome = await resolver.resolve(
            request(),
            FakeModelProvider(default_response=intent_content("void")),
        )
        assert isinstance(outcome, ResolvedIntent)
        dumped = json.dumps(outcome.value_resolution.model_dump())
        assert "void" not in dumped
        assert '"status": "warned"' in dumped

    def test_outcome_model_never_serializes_values(self) -> None:
        outcome = ValueResolutionOutcome(
            snapshot_fingerprint=VIEW.catalog_fingerprint,
        )
        assert set(outcome.model_dump()) == {"snapshot_fingerprint", "filters"}
