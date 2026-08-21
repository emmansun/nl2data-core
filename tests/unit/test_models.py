"""Unit tests for public model validation, immutability and bounds."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nl2data import (
    EngineCapabilitySnapshot,
    OutcomeStatus,
    QueryContext,
    QueryOptions,
    QueryOutcome,
    QueryRequest,
    QueryResult,
)


class TestPublicModelsRejectUnknownFields:
    def test_query_request_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            QueryRequest(request_id="r1", prompt="q", unexpected=1)

    def test_query_result_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            QueryResult(result_id="res-1", native_cursor="<cursor>")

    def test_outcome_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            QueryOutcome(status=OutcomeStatus.SUCCEEDED, request_id="r1", raw_state={})


class TestPublicModelsAreImmutable:
    def test_query_request_cannot_be_mutated(self) -> None:
        request = QueryRequest(request_id="r1", prompt="q")
        with pytest.raises(ValidationError):
            request.prompt = "changed"  # type: ignore[misc]

    def test_capability_snapshot_cannot_be_mutated(self) -> None:
        snapshot = EngineCapabilitySnapshot(plugins=frozenset({"a@1.0.0"}))
        with pytest.raises(ValidationError):
            snapshot.plugins = frozenset()  # type: ignore[misc]


class TestBoundedValues:
    def test_options_bounds(self) -> None:
        with pytest.raises(ValidationError):
            QueryOptions(max_attempts=0)
        with pytest.raises(ValidationError):
            QueryOptions(timeout_seconds=0.0)

    def test_prompt_bounds(self) -> None:
        with pytest.raises(ValidationError):
            QueryRequest(request_id="r1", prompt="")
        with pytest.raises(ValidationError):
            QueryRequest(request_id="r1", prompt="x" * 100_001)

    def test_result_rows_are_scalar_only(self) -> None:
        with pytest.raises(ValidationError):
            QueryResult(result_id="res-1", rows=((object(),),))

    def test_result_accepts_scalar_rows(self) -> None:
        result = QueryResult(
            result_id="res-1",
            column_names=("count",),
            rows=((42,), ("text",), (None,), (True,), (1.5,)),
        )
        assert len(result.rows) == 5

    def test_context_is_opaque_correlation_only(self) -> None:
        context = QueryContext(request_id="r1")
        assert context.request_id == "r1"
        assert context.workflow_id is None
