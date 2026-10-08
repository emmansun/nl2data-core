"""Stable, bounded, value-safe diagnostics for the Wren MDL importer.

The importer is a format adapter in front of the existing semantic
assembly authoring contract.  Its failure surface is part of the public
contract: every failure carries a stable code from the closed family
below, a normalized JSON-style source path, an optional one-based
line/column mark, and a controlled message that never echoes input
values, SQL fragments, credentials, or unbounded parser exceptions.

Code family (stable; extend only with new members, never by renaming):

- ``invalid_input``            unreadable, non-UTF-8, oversized, too
  deep, duplicate JSON keys, or malformed JSON.
- ``unsupported_version``      missing or unknown ``schemaVersion``.
- ``unsupported_construct``    a documented-unsupported or unknown
  semantic structure, including executable SQL and physical
  deployment/lifecycle content.
- ``missing_mapping``          a required identity or reference cannot
  be mapped (no override supplied).
- ``ambiguous_reference``      a reference resolves to more than one
  candidate or an equality endpoint is ambiguous.
- ``identifier_collision``     normalized identities collide and no
  override resolves the collision.
- ``unsupported_expression``   a calculated expression does not fit the
  governed ``ExprNode`` tree or a measure is unrepresentable.
- ``invalid_manifest``         the optional mapping manifest is
  malformed or out of bounds.
- ``authoring_validation_failed``
  the generated authoring YAML failed the existing
  ``SemanticAssemblyAuthoringLoader``.
- ``output_exists``            CLI path handling refused a write.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: Maximum diagnostics returned for one import; the rest are summarized
#: through the ``truncated`` flag on the result.
MAX_MDL_DIAGNOSTICS = 100
_MAX_MESSAGE_CHARS = 256
_MAX_PATH_PARTS = 64

MdlDiagnosticCode = Literal[
    "invalid_input",
    "unsupported_version",
    "unsupported_construct",
    "missing_mapping",
    "ambiguous_reference",
    "identifier_collision",
    "unsupported_expression",
    "invalid_manifest",
    "authoring_validation_failed",
    "output_exists",
]


class MdlSourceMark(BaseModel):
    """One-based line/column location inside the MDL source text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    line: int = Field(ge=1, le=2**31 - 1)
    column: int = Field(ge=1, le=2**31 - 1)


class MdlDiagnostic(BaseModel):
    """One bounded, value-safe importer diagnostic."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: MdlDiagnosticCode
    path: str = Field(min_length=1, max_length=1_024)
    mark: MdlSourceMark | None = None
    message: str = Field(min_length=1, max_length=_MAX_MESSAGE_CHARS)


class MdlImportResult(BaseModel):
    """Result of one offline MDL import attempt.

    ``document`` is set only when the complete conversion passed the
    existing authoring loader; a failed import never returns a partial
    document.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    document: str | None = Field(default=None, max_length=1_048_576)
    diagnostics: tuple[MdlDiagnostic, ...] = Field(
        default_factory=tuple,
        max_length=MAX_MDL_DIAGNOSTICS,
    )
    truncated: bool = False

    @property
    def successful(self) -> bool:
        return self.document is not None and not self.diagnostics


class DiagnosticSink:
    """Collects diagnostics in deterministic order with a hard bound."""

    def __init__(self) -> None:
        self.items: list[MdlDiagnostic] = []
        self.truncated = False

    def add(
        self,
        code: MdlDiagnosticCode,
        path: tuple[str | int, ...],
        message: str,
        *,
        mark: MdlSourceMark | None = None,
    ) -> None:
        if len(self.items) >= MAX_MDL_DIAGNOSTICS:
            self.truncated = True
            return
        self.items.append(
            MdlDiagnostic(
                code=code,
                path=render_path(path),
                mark=mark,
                message=message[:_MAX_MESSAGE_CHARS],
            )
        )

    def result(self, document: str | None) -> MdlImportResult:
        # Sorting by (path, code, message) keeps diagnostics identical for
        # equivalent inputs regardless of JSON member presentation order.
        ordered = sorted(self.items, key=lambda item: (item.path, item.code, item.message))
        return MdlImportResult(
            document=document,
            diagnostics=tuple(ordered),
            truncated=self.truncated,
        )


def render_path(parts: tuple[str | int, ...]) -> str:
    """Render a bounded JSON-style path like ``$.models[0].columns[2]``."""
    path = "$"
    for part in parts[:_MAX_PATH_PARTS]:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path
