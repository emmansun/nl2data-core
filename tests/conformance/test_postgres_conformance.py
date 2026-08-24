"""Optional PostgreSQL conformance execution.

Shares the SQLite conformance expectations through the controlled fixture
profile: the same logical schema, seed, policy cases, and protected result
assertions.  The driver and service are optional; when either is unavailable
the outcome is skipped or unavailable - never a pass.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from nl2data_core.adapters.sql.compile import compile_ir
from nl2data_core.adapters.sql.execution import execute_sql
from nl2data_core.fixtures import (
    FIXTURE_SPEC,
    POLICY_CASES,
    POSTGRES_FIXTURE_SPEC,
    RESULT_ASSERTIONS,
    PostgresFixtureProfile,
    SQLiteFixtureProfile,
)
from nl2data_core.fixtures.models import FixtureUnavailableError
from nl2data_core.governance.decisions import PolicyEvaluator
from nl2data_core.planning.ir.models import (
    IRFilter,
    IROrdering,
    IRProvenance,
    IRSelection,
    SemanticQueryIR,
)
from nl2data_core.planning.models import ColumnBinding, PhysicalBinding

FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
EMEA_TOP3 = ((18, 180.0), (17, 170.0), (16, 160.0))


def make_binding(**overrides) -> PhysicalBinding:
    values = {
        "object_id": "orders",
        "dialect": "postgres",
        "column_bindings": (
            ColumnBinding(field_id="order_id", physical_name="order_id"),
            ColumnBinding(field_id="amount", physical_name="amount"),
            ColumnBinding(field_id="region", physical_name="region"),
        ),
    }
    values.update(overrides)
    return PhysicalBinding(**values)


def make_ir(**overrides) -> SemanticQueryIR:
    values = {
        "ir_id": "conformance-ir",
        "source_id": "sales",
        "root_entity_id": "order",
        "selections": (
            IRSelection(selection_id="s1", field_id="order_id", alias="oid"),
            IRSelection(selection_id="s2", field_id="amount", alias="amt"),
        ),
        "filters": (
            IRFilter(filter_id="f1", field_id="region", operator="eq", value="emea"),
        ),
        "orderings": (IROrdering(ordering_id="o1", field_id="amount", direction="desc"),),
        "limit": 3,
        "provenance": IRProvenance(source_id="sales", root_entity_id="order"),
    }
    values.update(overrides)
    return SemanticQueryIR(**values)


def _require_postgres_driver() -> None:
    """Skip cleanly when the optional driver is absent (skipped outcome)."""
    if not PostgresFixtureProfile.driver_available():
        pytest.skip("psycopg is not installed; the postgres conformance profile is skipped")


def _require_postgres_service(profile: PostgresFixtureProfile) -> None:
    """Provision or skip; an unreachable service is never reported as a pass."""
    try:
        profile.provision()
    except FixtureUnavailableError:
        pytest.skip(
            "postgresql service is unavailable; the postgres conformance profile is skipped"
        )


class TestPostgresConformance:
    def test_profile_shares_sqlite_expectations(self) -> None:
        assert POSTGRES_FIXTURE_SPEC.fixture_id == FIXTURE_SPEC.fixture_id
        assert POSTGRES_FIXTURE_SPEC.expected_counts == FIXTURE_SPEC.expected_counts

    def test_conformance_is_skipped_without_driver(self) -> None:
        """Without the optional driver the outcome is 'skipped', never a pass."""
        _require_postgres_driver()

    def test_unavailable_service_is_not_a_pass(self) -> None:
        """An unreachable service raises FixtureUnavailableError (unavailable)."""
        _require_postgres_driver()
        profile = PostgresFixtureProfile(
            dsn="postgresql://127.0.0.1:1/nl2data_nope?connect_timeout=1"
        )
        with pytest.raises(FixtureUnavailableError):
            profile.provision()

    def test_shared_result_assertions_pass_when_available(self) -> None:
        """The shared protected results hold against a real PostgreSQL service."""
        _require_postgres_driver()
        profile = PostgresFixtureProfile()
        _require_postgres_service(profile)
        try:
            profile.verify()
            for assertion in RESULT_ASSERTIONS:
                result = profile.run_query(assertion.sql)
                assert result.columns == assertion.columns, assertion.name
                assert result.rows == assertion.rows, assertion.name
                assert FINGERPRINT.fullmatch(result.fingerprint)
        finally:
            profile.dispose()

    def test_shared_policy_cases_pass_when_available(self) -> None:
        """The shared governance expectations hold without a database service."""
        _require_postgres_driver()
        evaluator = PolicyEvaluator()
        for case in POLICY_CASES:
            decision = evaluator.evaluate(case.facts, case.scope)
            assert decision.decision == case.expected, case.name

    def test_ir_compiles_and_executes_when_available(self) -> None:
        """A postgres-bound IR compiles and returns the shared protected rows."""
        _require_postgres_driver()
        profile = PostgresFixtureProfile()
        _require_postgres_service(profile)
        try:
            sql = compile_ir(make_ir(), binding=make_binding())
            result = profile.run_query(sql)
            assert result.columns == ("oid", "amt")
            assert result.rows == EMEA_TOP3
        finally:
            profile.dispose()

    def test_postgres_results_match_sqlite(self, tmp_path: Path) -> None:
        """Same logical data yields the same protected rows on both profiles."""
        _require_postgres_driver()
        profile = PostgresFixtureProfile()
        _require_postgres_service(profile)
        sqlite = SQLiteFixtureProfile(db_path=tmp_path / "fixture.db")
        sqlite.provision()
        try:
            for assertion in RESULT_ASSERTIONS:
                postgres = profile.run_query(assertion.sql)
                sqlite_result = execute_sql(assertion.sql, db_path=sqlite.db_path)
                assert postgres.columns == sqlite_result.columns, assertion.name
                assert postgres.rows == sqlite_result.rows, assertion.name
        finally:
            profile.dispose()
            sqlite.dispose()
