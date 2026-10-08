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
from demo.run.demo_deterministic import (
    GOVERNED_QUESTIONS,
    Q2_GRAPH,
    Q2_INTENT,
    Q2_IR,
    Q2_VIEW,
    Q6_GRAPH,
    Q6_INTENT,
    Q6_IR,
    Q6_VIEW,
)
from demo.run.demo_deterministic import run_demo as deterministic_demo
from demo.run.demo_deterministic import (
    run_governed_questions_demo as deterministic_governed_demo,
)
from demo.run.demo_deterministic import run_join_demo as deterministic_join_demo

from nl2data_core.planning.join_planner import JoinPlanner

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
class TestGovernedQuestionsDemo:
    async def test_governed_questions_demo_passes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nl2data-governed-demo-") as tmp:
            passed = await deterministic_governed_demo(db_dir=Path(tmp))
        assert passed

    def test_governed_questions_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nl2data-governed-demo-") as tmp:
            passed = asyncio.run(deterministic_governed_demo(db_dir=Path(tmp)))
        assert passed

    def test_all_standard_questions_are_governed(self) -> None:
        """q2/q5/q6/q8 are wired through the normal runtime path."""
        assert {question.key for question in GOVERNED_QUESTIONS} == {"q2", "q5", "q6", "q8"}
        for question in GOVERNED_QUESTIONS:
            assert question.prompt
            assert question.ir.provenance.source_id == "sales"
            assert question.golden_assertion

    def test_multi_entity_questions_pin_runtime_join_plan(self) -> None:
        """Multi-entity IR provenance pins the planner-produced join plan."""
        pinned = (
            (Q2_INTENT, Q2_GRAPH, Q2_VIEW, Q2_IR),
            (Q6_INTENT, Q6_GRAPH, Q6_VIEW, Q6_IR),
        )
        assert len([q for q in GOVERNED_QUESTIONS if q.graph is not None]) == 2
        for intent, graph, view, ir in pinned:
            outcome = JoinPlanner(graph, view).plan(intent)
            assert outcome.kind == "plan"
            assert outcome.plan is not None
            assert ir.root_entity_id == outcome.plan.root_entity_id
            assert ir.provenance.join_plan_fingerprint == outcome.plan.fingerprint

    def test_single_entity_questions_have_no_join_plan(self) -> None:
        assert all(q.graph is None for q in GOVERNED_QUESTIONS if q.key in {"q5", "q8"})
        assert all(
            q.ir.provenance.join_plan_fingerprint is None
            for q in GOVERNED_QUESTIONS
            if q.graph is None
        )


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
