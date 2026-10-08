"""Bounded, fail-closed JSON parsing for the Wren MDL importer.

The parser accepts only the pinned Wren MDL JSON manifest subset:

- ``schemaVersion`` must be exactly ``"1.1"`` (the documented
  engine-ready ``mdl.json`` schema generation this importer targets).
- Top-level members are restricted to ``catalog``, ``schema``,
  ``schemaVersion``, ``name``, ``description``, ``models``,
  ``relationships``, and ``metrics``.  ``views``, ``enumDefinitions``,
  and ``macros`` are tolerated only as empty arrays; any other member,
  and any non-empty content in the tolerated members, fails closed.
  ``metrics`` entries are semantically checked by the converter (only
  the narrow single-measure shape maps to the authoring contract).
- Duplicate JSON keys, invalid UTF-8, oversized documents, and
  over-deep nesting are rejected with typed diagnostics before any
  semantic work happens.

Structural checks cover the envelope only (lists of objects, string
names); semantic member checks live in the converter.
"""

from __future__ import annotations

import json
from typing import Any

from .diagnostics import DiagnosticSink, MdlDiagnostic, MdlSourceMark

#: Source payload and structural bounds (mirrors the authoring loader).
MAX_MDL_BYTES = 1_048_576
MAX_MDL_DEPTH = 64

#: The only upstream schema generation this importer understands.
SUPPORTED_MDL_VERSIONS = frozenset({"1.1"})

_TOP_LEVEL_MEMBERS = frozenset(
    {
        "catalog",
        "schema",
        "schemaVersion",
        "name",
        "description",
        "models",
        "relationships",
        "metrics",
    }
)
_EMPTY_LIST_TOLERATED = ("views", "enumDefinitions", "macros")


class _DuplicateKeyError(ValueError):
    """Raised by the object hook when a JSON object repeats a key."""


def _object_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key, value in pairs:
        if key in mapping:
            raise _DuplicateKeyError(key)
        mapping[key] = value
    return mapping


def _depth(value: Any) -> int:
    """Iterative maximum container nesting; never recurses on input."""
    maximum = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict):
            maximum = max(maximum, depth)
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            maximum = max(maximum, depth)
            stack.extend((child, depth + 1) for child in item)
    return maximum


def _wrong_type(
    sink: DiagnosticSink, path: tuple[str | int, ...], expected: str
) -> None:
    sink.add("unsupported_construct", path, f"expected {expected} at this position")


class MdlParseOutcome:
    """Parsed MDL document plus bounded diagnostics."""

    __slots__ = ("document", "diagnostics", "truncated")

    def __init__(
        self,
        document: dict[str, Any] | None,
        diagnostics: tuple[MdlDiagnostic, ...],
        *,
        truncated: bool = False,
    ) -> None:
        self.document = document
        self.diagnostics = diagnostics
        self.truncated = truncated

    @property
    def parsed(self) -> bool:
        return self.document is not None and not self.diagnostics


def parse_mdl(payload: bytes) -> MdlParseOutcome:
    """Parse one bounded MDL JSON payload; never raises on bad input."""
    sink = DiagnosticSink()

    if len(payload) > MAX_MDL_BYTES:
        sink.add("invalid_input", (), "the MDL document exceeds the byte limit")
        return _outcome(sink)

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        sink.add("invalid_input", (), "the MDL document is not valid UTF-8")
        return _outcome(sink)

    try:
        document = json.loads(text, object_pairs_hook=_object_pairs_hook)
    except _DuplicateKeyError:
        sink.add("invalid_input", (), "the MDL document contains duplicate JSON keys")
        return _outcome(sink)
    except json.JSONDecodeError as error:
        sink.add(
            "invalid_input",
            (),
            "the MDL document is not valid JSON",
            mark=MdlSourceMark(line=error.lineno, column=error.colno),
        )
        return _outcome(sink)
    except (RecursionError, ValueError):
        sink.add("invalid_input", (), "the MDL document could not be parsed safely")
        return _outcome(sink)

    if not isinstance(document, dict):
        sink.add("unsupported_construct", (), "the MDL document must be one JSON object")
        return _outcome(sink)

    if _depth(document) > MAX_MDL_DEPTH:
        sink.add("invalid_input", (), "the MDL document exceeds the nesting limit")
        return _outcome(sink)

    _check_envelope(sink, document)
    return _outcome(sink, document)


def _outcome(sink: DiagnosticSink, document: dict[str, Any] | None = None) -> MdlParseOutcome:
    if sink.items:
        return MdlParseOutcome(None, tuple(sink.items), truncated=sink.truncated)
    return MdlParseOutcome(document, ())


def _check_envelope(sink: DiagnosticSink, document: dict[str, Any]) -> None:
    unknown = sorted(set(document) - _TOP_LEVEL_MEMBERS - set(_EMPTY_LIST_TOLERATED))
    if unknown:
        sink.add(
            "unsupported_construct",
            (),
            "the MDL document contains an unsupported top-level member",
        )
        return

    version = document.get("schemaVersion")
    if not isinstance(version, str) or version not in SUPPORTED_MDL_VERSIONS:
        sink.add("unsupported_version", (), "the MDL schemaVersion is not supported")
        return

    for member in _EMPTY_LIST_TOLERATED:
        if member in document and document[member] != []:
            sink.add(
                "unsupported_construct",
                (member,),
                "only empty content is tolerated for this member",
            )
            return

    for member in ("catalog", "schema", "name", "description"):
        if member in document and not isinstance(document[member], str):
            _wrong_type(sink, (member,), "a string")
            return

    metrics = document.get("metrics", [])
    if not isinstance(metrics, list):
        _wrong_type(sink, ("metrics",), "an array")
        return
    for index, metric in enumerate(metrics):
        if not isinstance(metric, dict):
            _wrong_type(sink, ("metrics", index), "a metric object")
            return

    models = document.get("models")
    if "models" not in document or not models:
        sink.add("missing_mapping", ("models",), "at least one model is required")
        return
    if not isinstance(models, list):
        _wrong_type(sink, ("models",), "an array of models")
        return
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            _wrong_type(sink, ("models", index), "a model object")
            return

    relationships = document.get("relationships", [])
    if not isinstance(relationships, list):
        _wrong_type(sink, ("relationships",), "an array")
        return
    for index, relationship in enumerate(relationships):
        if not isinstance(relationship, dict):
            _wrong_type(sink, ("relationships", index), "a relationship object")
            return
