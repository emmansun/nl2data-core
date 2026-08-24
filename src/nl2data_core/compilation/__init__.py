"""Shared compiler governance boundary contracts (DDS-019).

Backend-neutral compilation context, evidence, guard results, and the
pre-execution guard boundary shared by every compiler and adapter.
"""

from .contract import (
    ArtifactGuardResult,
    CompilationContext,
    CompilationEvidence,
    CompilationIssue,
    CompileResult,
    IRCompiler,
    ResultLineageEvidence,
    artifact_guard_evidence_fingerprint,
    compilation_evidence_fingerprint,
    result_lineage_fingerprint,
    verify_pre_execution_guard,
)

__all__ = [
    "ArtifactGuardResult",
    "CompilationContext",
    "CompilationEvidence",
    "CompilationIssue",
    "CompileResult",
    "IRCompiler",
    "ResultLineageEvidence",
    "artifact_guard_evidence_fingerprint",
    "compilation_evidence_fingerprint",
    "result_lineage_fingerprint",
    "verify_pre_execution_guard",
]
