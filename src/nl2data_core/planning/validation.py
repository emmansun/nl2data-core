"""Authorized semantic view model shared by the governed paths.

The view bounds which sources, root entities, and fields a semantic IR
may reference; IR view-scope validation lives in
:mod:`nl2data_core.planning.ir.validation`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"


class AuthorizedView(BaseModel):
    """The authorized semantic view an IR may reference."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    root_entity_ids: frozenset[str] = Field(default_factory=frozenset)
    field_ids: frozenset[str] = Field(default_factory=frozenset)
    catalog_fingerprint: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    def contains_field(self, field_id: str) -> bool:
        return field_id in self.field_ids
