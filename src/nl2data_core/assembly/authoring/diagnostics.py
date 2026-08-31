"""Stable, bounded diagnostics and operation results for authoring APIs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nl2data_core.assembly.models import AssemblyDraft

from .models import SemanticAssemblyAuthoring

AuthoringDiagnosticCode = Literal[
    "invalid_encoding",
    "input_too_large",
    "invalid_yaml",
    "unsupported_yaml",
    "structure_limit",
    "incompatible_schema",
    "invalid_member",
    "duplicate_identity",
    "invalid_reference",
    "unsafe_content",
    "unsupported_export",
]


class _ResultModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AuthoringSourceMark(_ResultModel):
    line: int = Field(ge=1, le=2**31 - 1)
    column: int = Field(ge=1, le=2**31 - 1)


class AuthoringPath(_ResultModel):
    parts: tuple[str | int, ...] = Field(default_factory=tuple, max_length=64)

    def render(self) -> str:
        path = "$"
        for part in self.parts:
            path += f"[{part}]" if isinstance(part, int) else f".{part}"
        return path


class AuthoringDiagnostic(_ResultModel):
    code: AuthoringDiagnosticCode
    severity: Literal["error"] = "error"
    path: AuthoringPath = Field(default_factory=AuthoringPath)
    mark: AuthoringSourceMark | None = None
    message: str = Field(min_length=1, max_length=256)


class AuthoringSummary(_ResultModel):
    bundle_id: str = Field(min_length=1, max_length=128)
    model_version: str = Field(min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=128)
    entity_count: int = Field(ge=0, le=1_024)
    field_count: int = Field(ge=0, le=4_096)
    assertion_count: int = Field(default=0, ge=0, le=16_384)


class _DiagnosticResult(_ResultModel):
    diagnostics: tuple[AuthoringDiagnostic, ...] = Field(default_factory=tuple, max_length=100)
    issue_count: int = Field(default=0, ge=0, le=2**31 - 1)
    truncated: bool = False


class AuthoringParseResult(_DiagnosticResult):
    model: SemanticAssemblyAuthoring | None = None

    @property
    def loaded(self) -> bool:
        return self.model is not None and not self.diagnostics


class AuthoringValidationResult(_DiagnosticResult):
    model: SemanticAssemblyAuthoring | None = None
    summary: AuthoringSummary | None = None

    @property
    def valid(self) -> bool:
        return self.model is not None and not self.diagnostics


class AuthoringLoweringResult(_DiagnosticResult):
    draft: AssemblyDraft | None = None

    @property
    def lowered(self) -> bool:
        return self.draft is not None and not self.diagnostics


class AuthoringExportResult(_DiagnosticResult):
    document: str | None = Field(default=None, max_length=1_048_576)

    @property
    def exported(self) -> bool:
        return self.document is not None and not self.diagnostics
