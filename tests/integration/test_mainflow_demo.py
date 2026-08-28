"""Integration tests for the canonical mainflow demo (deterministic profile).

These tests exercise the demo entrypoints as an operator would, proving the
public facade path end to end without any external service dependencies.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
import yaml
from demo.run.demo_deterministic import run_demo as deterministic_demo
from demo.run.demo_deterministic import run_join_demo as deterministic_join_demo

#: Standard 10-question suite with SQL evidence and shape checks.
DEMO_QUESTIONS = Path(__file__).resolve().parents[2] / "demo" / "questions" / "questions.yml"


@pytest.mark.integration
class TestDeterministicMainflowDemo:
    async def test_deterministic_demo_passes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nl2data-demo-") as tmp:
            passed = await deterministic_demo(db_dir=Path(tmp))
        assert passed

    def test_deterministic_demo_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nl2data-demo-") as tmp:
            passed = asyncio.run(deterministic_demo(db_dir=Path(tmp)))
        assert passed


@pytest.mark.integration
class TestDeterministicJoinDemo:
    async def test_deterministic_join_demo_passes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nl2data-join-demo-") as tmp:
            passed = await deterministic_join_demo(db_dir=Path(tmp))
        assert passed

    def test_deterministic_join_demo_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nl2data-join-demo-") as tmp:
            passed = asyncio.run(deterministic_join_demo(db_dir=Path(tmp)))
        assert passed


@pytest.mark.integration
class TestDemoQuestionSuite:
    def test_question_suite_structure(self) -> None:
        """Every standard demo question carries a shape-checked evidence query."""
        suite = yaml.safe_load(DEMO_QUESTIONS.read_text(encoding="utf-8"))
        questions = suite["questions"]
        assert len(questions) == 10
        assert len({entry["id"] for entry in questions}) == 10
        for entry in questions:
            assert entry["text"]
            assert entry["capabilities"]
            assert entry["evidence_sql"].strip()
            shape = entry["shape_check"]
            assert shape["columns"]
            assert ("min_rows" in shape) != ("exact_rows" in shape)
