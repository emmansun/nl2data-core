"""Tests for the opt-in live OpenAI evaluation profile.

Proves the explicit ``skipped``/``unavailable``/``verified`` classification:
without credentials every case is ``skipped`` and no provider is built; with
an injected fake client every case is ``verified`` with protected evidence;
when the provider call fails the case is ``unavailable`` and never
``verified`` - all without the ``openai`` SDK or any network access.
"""

from __future__ import annotations

import json

from tests.provider.fake_openai import (
    AuthenticationError,
    FakeOpenAIClient,
    RateLimitError,
    fake_response,
)

from nl2data_core.ai.context import SemanticReference
from nl2data_core.ai.errors import ModelErrorCode
from nl2data_core.ai.evaluation.cases import build_ai_dataset
from nl2data_core.ai.evaluation.models import LiveAvailability
from nl2data_core.planning.validation import AuthorizedView
from nl2data_openai.config import OpenAIProviderConfig
from nl2data_openai.live_evaluation import run_live_openai_evaluation

VIEW = AuthorizedView(
    source_id="sales",
    root_entity_ids=frozenset({"order"}),
    field_ids=frozenset({"order_id", "amount", "status", "created_at"}),
    catalog_fingerprint="sha256:" + "a" * 64,
)

REFERENCES = {
    "order_id": SemanticReference(field_id="order_id", label="Order id"),
    "amount": SemanticReference(
        field_id="amount",
        label="Order amount",
        allowed_aggregations=frozenset({"sum", "avg"}),
    ),
    "status": SemanticReference(field_id="status", label="Order status"),
    "created_at": SemanticReference(field_id="created_at", label="Created at"),
}

VALID_ENVELOPE = json.dumps(
    {
        "intent": {
            "source_id": "sales",
            "root_entity_id": "order",
            "selections": [
                {"selection_id": "s1", "field_id": "amount", "aggregation": "sum"},
                {"selection_id": "s2", "field_id": "status"},
            ],
            "filters": [
                {
                    "filter_id": "f1",
                    "field_id": "status",
                    "operator": "eq",
                    "value": "shipped",
                }
            ],
            "orderings": [
                {"ordering_id": "o1", "field_id": "amount", "direction": "desc"}
            ],
            "limit": 100,
            "confidence": 0.9,
        },
        "clarification": None,
        "alternatives": None,
    }
)


def run_live(**overrides):
    values = {
        "dataset": build_ai_dataset(),
        "run_id": "live-1",
        "view": VIEW,
        "provider_config": OpenAIProviderConfig(model_name="gpt-4o-mini"),
        "semantic_references": REFERENCES,
    }
    values.update(overrides)
    return run_live_openai_evaluation(**values)


class TestSkippedProfile:
    async def test_without_credentials_every_case_is_skipped(self, monkeypatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        report = await run_live()
        assert report.verified_count == 0
        assert report.unavailable_count == 0
        assert report.skipped_count == len(build_ai_dataset().cases)
        for result in report.results:
            assert result.availability == LiveAvailability.SKIPPED
            assert result.skip_reason == "live OpenAI profile is not configured"
            assert result.evidence is None
            assert result.error is None

    async def test_skipped_report_is_deterministic(self, monkeypatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        first = await run_live()
        second = await run_live()
        assert first.fingerprint == second.fingerprint
        assert first.provider_name == "openai"
        assert first.model_name == "gpt-4o-mini"
        assert first.dataset_id == "ai-intent-boundary"

    async def test_skipped_report_serializes_safely(self, monkeypatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        report = await run_live()
        payload = report.to_json()
        assert '"availability": "skipped"' in payload
        # Skipped results carry classification fields and null evidence only.
        assert '"evidence": null' in payload
        assert '"prompt"' not in payload


class TestVerifiedProfile:
    async def test_configured_profile_verifies_every_case(self) -> None:
        cases = len(build_ai_dataset().cases)
        fake = FakeOpenAIClient([fake_response(VALID_ENVELOPE)] * cases)
        report = await run_live(client_factory=lambda: fake)
        assert report.verified_count == cases
        assert report.unavailable_count == 0
        assert report.skipped_count == 0
        for result in report.results:
            assert result.availability == LiveAvailability.VERIFIED
            assert result.error is None
            assert result.evidence is not None
            assert result.evidence.outcome == "resolved"
            assert result.evidence.call_count == 1
            assert result.evidence.intent_fingerprint.startswith("sha256:")

    async def test_verified_evidence_is_protected(self) -> None:
        cases = len(build_ai_dataset().cases)
        fake = FakeOpenAIClient([fake_response(VALID_ENVELOPE)] * cases)
        report = await run_live(client_factory=lambda: fake)
        for result in report.results:
            assert result.evidence is not None
            dumped = json.dumps(result.evidence.model_dump())
            # Raw prompt text and raw provider payload never enter evidence.
            assert "total shipped order amount" not in dumped
            assert VALID_ENVELOPE not in dumped
            # No key-shaped sensitive fields either.
            for token in ('"password"', '"credential"', '"secret"', '"content"'):
                assert token not in dumped

    async def test_verified_report_fingerprint_is_deterministic(self) -> None:
        cases = len(build_ai_dataset().cases)
        fake = FakeOpenAIClient([fake_response(VALID_ENVELOPE)] * cases)
        first = await run_live(client_factory=lambda: fake)
        fake = FakeOpenAIClient([fake_response(VALID_ENVELOPE)] * cases)
        second = await run_live(client_factory=lambda: fake)
        assert first.fingerprint == second.fingerprint
        assert first.fingerprint.startswith("sha256:")

    async def test_case_skip_reason_wins_over_verification(self) -> None:
        dataset = build_ai_dataset()
        first = dataset.cases[0].model_copy(update={"skip_reason": "not applicable"})
        dataset = dataset.model_copy(update={"cases": tuple([first, *dataset.cases[1:]])})
        fake = FakeOpenAIClient([fake_response(VALID_ENVELOPE)] * len(dataset.cases))
        report = await run_live(dataset=dataset, client_factory=lambda: fake)
        assert report.results[0].availability == LiveAvailability.SKIPPED
        assert report.results[0].skip_reason == "not applicable"
        assert report.results[0].evidence is None
        assert report.verified_count == len(dataset.cases) - 1


class TestUnavailableProfile:
    async def test_auth_failure_never_classifies_as_verified(self) -> None:
        cases = len(build_ai_dataset().cases)
        fake = FakeOpenAIClient([AuthenticationError("invalid key")] * cases)
        report = await run_live(client_factory=lambda: fake)
        assert report.verified_count == 0
        assert report.unavailable_count == cases
        for result in report.results:
            assert result.availability == LiveAvailability.UNAVAILABLE
            assert result.evidence is None
            assert result.error is not None
            assert result.error.code == ModelErrorCode.INVALID_REQUEST

    async def test_rate_limit_exhaustion_is_unavailable(self) -> None:
        cases = len(build_ai_dataset().cases)
        fake = FakeOpenAIClient([RateLimitError("slow down")] * (cases * 3))
        report = await run_live(client_factory=lambda: fake)
        assert report.verified_count == 0
        assert report.unavailable_count == cases
        for result in report.results:
            assert result.availability == LiveAvailability.UNAVAILABLE
            assert result.evidence is None
            assert result.error is not None
            assert result.error.code == ModelErrorCode.RETRY_EXHAUSTED
            assert result.error.details["attempts"] == "3"
