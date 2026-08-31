"""Verification contracts over equivalent SQLite, PostgreSQL, and Mongo results."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.test_verification_execution import (
    StubVerificationExecutor,
    _context,
    _ir,
    _semantic_case,
    _smoke_case,
)

from nl2data_core.adapters.sql.execution import execute_sql
from nl2data_core.fixtures import PostgresFixtureProfile, SQLiteFixtureProfile
from nl2data_core.fixtures.models import FixtureUnavailableError
from nl2data_core.verification import (
    AggregateTotalContract,
    OutcomeAssertion,
    RowCountAssertion,
    RowCountEqualityContract,
    ScalarEqualsAssertion,
    TaggedExpectedScalar,
)
from nl2data_core.verification.execution import VerificationObservation
from nl2data_core.verification.semantic import SemanticContractEvaluator
from nl2data_core.verification.smoke import SmokeVerificationEvaluator
from nl2data_mongodb.fixtures import MONGO_RESULT_ASSERTIONS, MongoFixtureProfile

_TOP_THREE = ((18, 180.0), (17, 170.0), (16, 160.0))


def _observation(rows: tuple[tuple[object, ...], ...]) -> VerificationObservation:
    scope = "sha256:" + "a" * 64
    return VerificationObservation(
        status="succeeded",
        executor_id="stub-verification",
        executor_capability_fingerprint=scope,
        bundle_fingerprint=_context().candidate.fingerprint,
        ir_fingerprint=_ir().fingerprint,
        fixture_setup_fingerprint=scope,
        selection_ids=("order", "amount"),
        rows=rows,
        result_fingerprint=scope,
    )


async def _assert_shared_contracts(rows: tuple[tuple[object, ...], ...]) -> None:
    executor = StubVerificationExecutor(_observation(rows))
    smoke = await SmokeVerificationEvaluator(executor=executor).evaluate_case(
        _smoke_case(
            OutcomeAssertion(assertion_id="outcome", expected="success"),
            RowCountAssertion(assertion_id="rows", minimum=3, maximum=3),
            ScalarEqualsAssertion(
                assertion_id="top-order",
                selection_id="order",
                expected=TaggedExpectedScalar(kind="int", value=18),
            ),
        ),
        _context(),
    )
    semantic = await SemanticContractEvaluator(executor=executor).evaluate_case(
        _semantic_case(
            RowCountEqualityContract(assertion_id="rows", expected=3),
            AggregateTotalContract(
                assertion_id="total",
                selection_id="amount",
                expected=TaggedExpectedScalar(kind="decimal", value="510"),
            ),
        ),
        _context(),
    )
    assert smoke.status.value == "passed"
    assert semantic.status.value == "passed"


@pytest.mark.asyncio
async def test_sqlite_verification_fixture_runs_shared_contracts(tmp_path: Path) -> None:
    profile = SQLiteFixtureProfile(tmp_path / "verification-conformance.db")
    profile.provision()
    try:
        result = execute_sql(
            "SELECT order_id AS oid, amount AS amt FROM orders "
            "WHERE region = 'emea' ORDER BY amount DESC LIMIT 3",
            db_path=profile.db_path,
        )
        assert result.rows == _TOP_THREE
        await _assert_shared_contracts(result.rows)
    finally:
        profile.dispose()


@pytest.mark.asyncio
async def test_postgres_verification_fixture_runs_shared_contracts_when_available() -> None:
    if not PostgresFixtureProfile.driver_available():
        pytest.skip("psycopg is unavailable; PostgreSQL verification is skipped")
    profile = PostgresFixtureProfile()
    try:
        profile.provision()
    except FixtureUnavailableError:
        pytest.skip("PostgreSQL is unavailable; verification is skipped")
    try:
        result = profile.run_query(
            "SELECT order_id AS oid, amount AS amt FROM orders "
            "WHERE region = 'emea' ORDER BY amount DESC LIMIT 3"
        )
        await _assert_shared_contracts(result.rows)
    finally:
        profile.dispose()


@pytest.mark.asyncio
async def test_mongo_controlled_fixture_runs_shared_contracts() -> None:
    profile = MongoFixtureProfile()
    profile.provision()
    try:
        assertion = next(
            item
            for item in MONGO_RESULT_ASSERTIONS
            if item.name == "emea orders by amount desc"
        )
        by_column = [
            dict(zip(assertion.columns, row, strict=True)) for row in assertion.rows
        ]
        rows = tuple((row["order_id"], row["amount"]) for row in by_column)
        assert rows == _TOP_THREE
        await _assert_shared_contracts(rows)
    finally:
        profile.dispose()