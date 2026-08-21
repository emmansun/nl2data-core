"""Integration tests for controlled SQL fixture profiles.

Distinguishes passing, failing, skipped, and unavailable outcomes for the
default SQLite profile and the optional PostgreSQL profile.  PostgreSQL is
never reported as passing when its driver or service is absent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nl2data.errors import ErrorCode
from nl2data_core.adapters.sql.execution import execute_sql
from nl2data_core.fixtures import (
    FIXTURE_SETUP_FINGERPRINT,
    FIXTURE_SPEC,
    POLICY_CASES,
    RESULT_ASSERTIONS,
    SCHEMA,
    SEED,
    PostgresFixtureProfile,
    SQLiteFixtureProfile,
    make_fixture_spec,
)
from nl2data_core.fixtures.models import FixtureUnavailableError, FixtureVerificationError
from nl2data_core.governance.decisions import PolicyEvaluator


def make_sqlite(tmp_path: Path) -> SQLiteFixtureProfile:
    return SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")


def tamper_orders(db_path: Path) -> None:
    """Remove one seed order so expected counts no longer match."""
    connection = sqlite3.connect(str(db_path))
    connection.execute("DELETE FROM orders WHERE order_id = 1")
    connection.commit()
    connection.close()


class TestSQLiteFixture:
    def test_spec_matches_seed_data(self) -> None:
        assert FIXTURE_SPEC.expected_count("orders") == len(SEED["orders"])
        assert FIXTURE_SPEC.expected_count("customers") == len(SEED["customers"])
        assert FIXTURE_SPEC.setup_fingerprint.startswith("sha256:")

    def test_provision_creates_expected_counts(self, tmp_path: Path) -> None:
        profile = make_sqlite(tmp_path)
        profile.provision()
        profile.verify()
        assert profile.db_path.exists()
        assert profile.stored_setup_fingerprint() == FIXTURE_SETUP_FINGERPRINT

    def test_provision_is_repeatable(self, tmp_path: Path) -> None:
        first = make_sqlite(tmp_path)
        first.provision()
        first_fingerprint = first.stored_setup_fingerprint()
        first_result = execute_sql(
            "SELECT order_id, amount FROM orders ORDER BY order_id LIMIT 5",
            db_path=first.db_path,
        )
        first.dispose()

        second = make_sqlite(tmp_path)
        second.provision()
        second_result = execute_sql(
            "SELECT order_id, amount FROM orders ORDER BY order_id LIMIT 5",
            db_path=second.db_path,
        )
        assert second.stored_setup_fingerprint() == first_fingerprint
        assert second_result.fingerprint == first_result.fingerprint
        assert second_result.rows == first_result.rows

    def test_verify_fails_after_tampering(self, tmp_path: Path) -> None:
        profile = make_sqlite(tmp_path)
        profile.provision()
        tamper_orders(profile.db_path)
        with pytest.raises(FixtureVerificationError) as excinfo:
            profile.verify()
        assert excinfo.value.code == ErrorCode.FIXTURE_VERIFICATION_FAILED
        assert "orders" in excinfo.value.message

    def test_reset_restores_seed_state(self, tmp_path: Path) -> None:
        profile = make_sqlite(tmp_path)
        profile.provision()
        tamper_orders(profile.db_path)
        profile.reset()
        profile.verify()

    def test_truncate_reseed_strategy_restores_counts(self, tmp_path: Path) -> None:
        profile = SQLiteFixtureProfile(
            db_path=tmp_path / "fixture.db",
            spec=make_fixture_spec(dialect="sqlite", reset_strategy="truncate_reseed"),
        )
        profile.provision()
        tamper_orders(profile.db_path)
        profile.reset()
        profile.verify()

    def test_dispose_removes_database_file(self, tmp_path: Path) -> None:
        profile = make_sqlite(tmp_path)
        profile.provision()
        assert profile.db_path.exists()
        profile.dispose()
        assert not profile.db_path.exists()

    def test_result_assertions_hold(self, tmp_path: Path) -> None:
        profile = make_sqlite(tmp_path)
        profile.provision()
        for assertion in RESULT_ASSERTIONS:
            result = execute_sql(assertion.sql, db_path=profile.db_path)
            assert result.columns == assertion.columns, assertion.name
            assert result.rows == assertion.rows, assertion.name

    def test_policy_cases_hold(self) -> None:
        evaluator = PolicyEvaluator()
        for case in POLICY_CASES:
            decision = evaluator.evaluate(case.facts, case.scope)
            assert decision.decision == case.expected, case.name


def _require_postgres_driver() -> None:
    """Skip cleanly when the optional driver is absent (skipped outcome)."""
    if not PostgresFixtureProfile.driver_available():
        pytest.skip("psycopg is not installed; the postgres profile is skipped")


def _require_postgres_service(profile: PostgresFixtureProfile) -> None:
    """Provision or skip; an unreachable service is never reported as a pass."""
    try:
        profile.provision()
    except FixtureUnavailableError:
        pytest.skip("postgresql service is unavailable; the postgres profile is skipped")


class TestPostgresFixture:
    def test_profile_is_skipped_without_driver(self) -> None:
        """Without the optional driver the outcome is 'skipped', never a pass."""
        _require_postgres_driver()

    def test_profile_is_unavailable_when_service_is_down(self) -> None:
        """An unreachable service raises FixtureUnavailableError (unavailable outcome)."""
        _require_postgres_driver()
        profile = PostgresFixtureProfile(
            dsn="postgresql://127.0.0.1:1/nl2data_nope?connect_timeout=1"
        )
        with pytest.raises(FixtureUnavailableError) as excinfo:
            profile.provision()
        assert excinfo.value.code == ErrorCode.FIXTURE_UNAVAILABLE
        assert excinfo.value.category.value == "adapter"

    def test_profile_passes_when_service_is_available(self) -> None:
        """Full passing outcome: counts, shared result assertions, policy cases."""
        _require_postgres_driver()
        profile = PostgresFixtureProfile()
        _require_postgres_service(profile)
        try:
            profile.verify()
            for assertion in RESULT_ASSERTIONS:
                result = profile.run_query(assertion.sql)
                assert result.columns == assertion.columns, assertion.name
                assert result.rows == assertion.rows, assertion.name
            evaluator = PolicyEvaluator()
            for case in POLICY_CASES:
                decision = evaluator.evaluate(case.facts, case.scope)
                assert decision.decision == case.expected, case.name
        finally:
            profile.dispose()

    def test_profile_verification_failure_is_not_a_pass(self) -> None:
        """A tampered fixture fails verification; the outcome is 'fail', never 'pass'."""
        _require_postgres_driver()
        profile = PostgresFixtureProfile()
        _require_postgres_service(profile)
        try:
            with profile.connect() as connection:
                connection.execute("DELETE FROM orders WHERE order_id = 1")
                connection.commit()
            with pytest.raises(FixtureVerificationError):
                profile.verify()
        finally:
            profile.dispose()

    def test_schema_is_shared_with_sqlite(self) -> None:
        """The PostgreSQL profile provisions the same logical tables."""
        assert PostgresFixtureProfile().spec.fixture_id == FIXTURE_SPEC.fixture_id
        assert set(SCHEMA) == {"customers", "orders"}
