"""Deterministic JSON report rendering and persistence.

Reports are rendered with sorted keys and a stable fingerprint so equal
runs produce byte-equal output; durations and other environmental values
never affect the fingerprint.
"""

from __future__ import annotations

from pathlib import Path

from nl2data_core.evaluation.models import EvaluationReport


def render_report(report: EvaluationReport) -> str:
    """Render the report as deterministic, sorted JSON."""
    return report.to_json()


def write_report(report: EvaluationReport, path: Path) -> None:
    """Persist the deterministic JSON report to ``path`` (UTF-8)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(report), encoding="utf-8")
