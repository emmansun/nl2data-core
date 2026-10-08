"""Bounded optional identifier-override manifest for MDL imports.

The manifest lets a caller resolve identifier normalization problems
explicitly instead of letting the importer guess:

.. code-block:: json

    {
      "version": "1",
      "entities": {"Orders": "orders"},
      "fields": {"Orders.customer_id": "customer_id"},
      "calculated": {"Orders.net_amount": "net_amount"},
      "relationships": {"OrdersCustomers": "orders_customers"},
      "measures": {"net_revenue": "net_revenue"}
    }

Keys are source names (model name, ``Model.column``, ``Model.calculated``,
or relationship name); values must be valid authoring identifiers.  The
manifest never maps anything except identities: it cannot inject fields,
policies, physical bindings, or lifecycle state.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .diagnostics import MdlDiagnostic

MAX_MANIFEST_BYTES = 65_536
MAX_MANIFEST_ENTRIES = 4_096

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-.]{0,127}$")

_SECTIONS = ("entities", "fields", "calculated", "relationships", "measures")


class _DuplicateManifestKeyError(ValueError):
    """Raised by the object hook when the manifest repeats a key."""


def _unique_object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    mapping: dict[str, object] = {}
    for key, value in pairs:
        if key in mapping:
            raise _DuplicateManifestKeyError(key)
        mapping[key] = value
    return mapping


@dataclass(frozen=True)
class MappingManifest:
    """Validated override tables, keyed by section then source name."""

    entities: dict[str, str] = field(default_factory=dict)
    fields: dict[str, str] = field(default_factory=dict)
    calculated: dict[str, str] = field(default_factory=dict)
    relationships: dict[str, str] = field(default_factory=dict)
    measures: dict[str, str] = field(default_factory=dict)

    def identifier_for(self, section: str, key: str) -> str | None:
        table: dict[str, str] = getattr(self, section)
        return table.get(key)


def load_manifest(payload: bytes) -> tuple[MappingManifest | None, tuple[MdlDiagnostic, ...]]:
    """Parse and validate one mapping manifest payload."""
    if len(payload) > MAX_MANIFEST_BYTES:
        return None, (
            MdlDiagnostic(
                code="invalid_manifest",
                path="$",
                message="the mapping manifest exceeds the byte limit",
            ),
        )

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None, (
            MdlDiagnostic(
                code="invalid_manifest",
                path="$",
                message="the mapping manifest is not valid UTF-8",
            ),
        )

    try:
        document = json.loads(text, object_pairs_hook=_unique_object_pairs)
    except _DuplicateManifestKeyError:
        return None, (
            MdlDiagnostic(
                code="invalid_manifest",
                path="$",
                message="the mapping manifest contains duplicate JSON keys",
            ),
        )
    except json.JSONDecodeError:
        return None, (
            MdlDiagnostic(
                code="invalid_manifest",
                path="$",
                message="the mapping manifest is not valid JSON",
            ),
        )

    if not isinstance(document, dict):
        return None, (
            MdlDiagnostic(
                code="invalid_manifest",
                path="$",
                message="the mapping manifest must be one JSON object",
            ),
        )

    unknown = sorted(set(document) - {"version", *_SECTIONS})
    if unknown or (document.get("version") != "1"):
        return None, (
            MdlDiagnostic(
                code="invalid_manifest",
                path="$",
                message="the mapping manifest version or members are not supported",
            ),
        )

    sections: dict[str, dict[str, str]] = {}
    for section in _SECTIONS:
        entries = document.get(section, {})
        if not isinstance(entries, dict) or len(entries) > MAX_MANIFEST_ENTRIES:
            return None, (
                MdlDiagnostic(
                    code="invalid_manifest",
                    path=f"$.{section}",
                    message="the manifest section is out of bounds",
                ),
            )
        mapping: dict[str, str] = {}
        for key, value in entries.items():
            if not isinstance(key, str) or not isinstance(value, str):
                return None, (
                    MdlDiagnostic(
                        code="invalid_manifest",
                        path=f"$.{section}",
                        message="manifest keys and values must be strings",
                    ),
                )
            if not _IDENTIFIER_RE.match(value):
                return None, (
                    MdlDiagnostic(
                        code="invalid_manifest",
                        path=f"$.{section}",
                        message="a manifest override is not a valid identifier",
                    ),
                )
            mapping[key] = value
        sections[section] = mapping

    return MappingManifest(**sections), ()
