"""Deterministic evaluation runner models for the P1 skeleton.

Cases bind validated semantic IRs to controlled fixtures; evidence and
reports carry only protected fingerprints and scalar values, so native
clients, credentials, and raw prompts never cross the evaluation boundary.
"""

from __future__ import annotations

from nl2data_core.evaluation.models import (
    AssertionResult,
    CaseEvidence,
    CaseOutcome,
    CaseResult,
    EvaluationCase,
    EvaluationDataset,
    EvaluationReport,
    EvaluationRunContext,
    MandatoryAssertion,
)
from nl2data_core.evaluation.reporting import render_report, write_report
from nl2data_core.evaluation.runner import (
    CaseExecutor,
    EvaluationRunner,
    evaluate_assertions,
    evidence_is_redacted,
)
from nl2data_core.evaluation.sqlite_executor import SqliteCaseExecutor

__all__ = [
    "AssertionResult",
    "CaseEvidence",
    "CaseExecutor",
    "CaseOutcome",
    "CaseResult",
    "EvaluationCase",
    "EvaluationDataset",
    "EvaluationReport",
    "EvaluationRunContext",
    "EvaluationRunner",
    "MandatoryAssertion",
    "SqliteCaseExecutor",
    "evaluate_assertions",
    "evidence_is_redacted",
    "render_report",
    "write_report",
]
