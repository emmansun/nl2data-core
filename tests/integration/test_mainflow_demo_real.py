"""Real-service integration tests for the canonical mainflow demo.

These tests exercise the demo against PostgreSQL and optional Redis. When the
driver is missing, the DSN is not configured, the service is unreachable, or
the reference dataset is not seeded the outcome is skipped - never a pass.
"""

from __future__ import annotations

import asyncio
import os
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import pytest
import yaml
from demo.run.demo_deterministic import run_demo as deterministic_demo
from demo.run.demo_real_service import run_join_demo as real_service_join_demo

pytestmark = pytest.mark.integration

#: Service location; override with NL2DATA_POSTGRES_DSN for CI/dev services.
DSN = os.environ.get("NL2DATA_POSTGRES_DSN")

#: Standard 10-question suite with SQL evidence and shape checks.
DEMO_QUESTIONS = (
    Path(__file__).resolve().parents[2] / "demo" / "questions" / "questions.yml"
)


def _require_postgres_service() -> Any:
    """Connect to PostgreSQL or skip; an unreachable service is never a pass."""
    if DSN is None:
        pytest.skip(
            "NL2DATA_POSTGRES_DSN is not set; the real-service demo profile is skipped"
        )
    if find_spec("psycopg") is None:
        pytest.skip(
            "psycopg is not installed; the real-service demo profile is skipped"
        )
    try:
        connection = import_module("psycopg").connect(DSN, connect_timeout=2.0)
    except Exception:
        pytest.skip(
            "postgres service is unavailable; the real-service demo profile is skipped"
        )
    return connection


def _require_seeded_dataset(connection: Any) -> None:
    """Skip when the reference tables were not seeded (skipped outcome).

    Note: the ``with connection:`` transaction context closes the connection
    on exit (psycopg >= 3.3), so the probe executes without it and the caller
    keeps the connection for subsequent queries.
    """
    try:
        connection.execute("SELECT 1 FROM orders LIMIT 1").fetchone()
    except Exception:
        pytest.skip(
            "demo dataset is not seeded; run python demo/seed/seed.py --scale small"
        )


class TestRealServiceMainflowDemo:
    async def test_real_service_demo_passes(self) -> None:
        connection = _require_postgres_service()
        _require_seeded_dataset(connection)
        connection.close()
        assert DSN is not None
        from demo.run.demo_real_service import run_demo as real_service_demo

        passed = await real_service_demo(
            dsn=DSN,
            redis_url=os.environ.get("NL2DATA_REDIS_URL"),
            run_id="real-a",
        )
        assert passed

    def test_real_service_demo_entrypoint(self) -> None:
        connection = _require_postgres_service()
        _require_seeded_dataset(connection)
        connection.close()
        assert DSN is not None
        from demo.run.demo_real_service import run_demo as real_service_demo

        passed = asyncio.run(
            real_service_demo(
                dsn=DSN,
                redis_url=os.environ.get("NL2DATA_REDIS_URL"),
                run_id="real-b",
            )
        )
        assert passed


class TestDemoEvidenceSuite:
    def test_evidence_queries_match_shape(self) -> None:
        """Every seeded evidence query returns its declared column shape."""
        connection = _require_postgres_service()
        _require_seeded_dataset(connection)
        suite = yaml.safe_load(DEMO_QUESTIONS.read_text(encoding="utf-8"))
        try:
            for entry in suite["questions"]:
                cursor = connection.execute(entry["evidence_sql"])
                try:
                    columns = [column.name for column in cursor.description]
                    rows = cursor.fetchall()
                finally:
                    cursor.close()
                assert columns == entry["shape_check"]["columns"], entry["id"]
                shape = entry["shape_check"]
                if "exact_rows" in shape:
                    assert len(rows) == shape["exact_rows"], entry["id"]
                else:
                    assert len(rows) >= shape["min_rows"], entry["id"]
        finally:
            connection.close()


class TestRealServiceJoinDemo:
    async def test_real_service_join_demo_passes(self) -> None:
        connection = _require_postgres_service()
        _require_seeded_dataset(connection)
        connection.close()
        assert DSN is not None
        redis_url = os.environ.get("NL2DATA_REDIS_URL")
        passed = await real_service_join_demo(
            dsn=DSN, redis_url=redis_url, run_id="join-a"
        )
        assert passed

    def test_real_service_join_demo_entrypoint(self) -> None:
        connection = _require_postgres_service()
        _require_seeded_dataset(connection)
        connection.close()
        assert DSN is not None
        redis_url = os.environ.get("NL2DATA_REDIS_URL")
        passed = asyncio.run(
            real_service_join_demo(dsn=DSN, redis_url=redis_url, run_id="join-b")
        )
        assert passed


class TestDeterministicProfileStillRuns:
    async def test_deterministic_demo_passes(self, tmp_path: Path) -> None:
        passed = await deterministic_demo(db_dir=tmp_path)
        assert passed
