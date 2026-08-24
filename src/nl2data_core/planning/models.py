"""Backend-neutral physical binding models for compiler context.

The Literal kinds shared by the canonical Semantic Query IR live here so
the IR package never imports adapter code.  ``PhysicalBinding`` is
explicit compiler context: it never enters IR serialization.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"

AggregationKind = Literal["none", "count", "sum", "avg", "min", "max"]
FilterOperator = Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains"]
OrderDirection = Literal["asc", "desc"]

#: Public scalar set; anything else is a driver-native value and is rejected.
SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool, type(None))


class ColumnBinding(BaseModel):
    """Physical column binding for one semantic field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    physical_name: str = Field(pattern=_IDENTIFIER_PATTERN)


class PhysicalBinding(BaseModel):
    """Minimal physical binding used to compile IR cases.

    Contains physical names only - never SQL AST nodes or driver objects.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    object_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    dialect: str = Field(min_length=1, max_length=32)
    column_bindings: tuple[ColumnBinding, ...] = Field(default_factory=tuple)

    def physical_name(self, field_id: str) -> str | None:
        for binding in self.column_bindings:
            if binding.field_id == field_id:
                return binding.physical_name
        return None

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "dialect": self.dialect,
            "column_bindings": [binding.model_dump() for binding in self.column_bindings],
        }
