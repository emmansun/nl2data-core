"""Bounded, non-executable YAML loading for semantic authoring documents."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

import yaml
from pydantic import ValidationError
from yaml.events import AliasEvent, CollectionEndEvent, CollectionStartEvent, NodeEvent
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from .diagnostics import (
    AuthoringDiagnostic,
    AuthoringParseResult,
    AuthoringPath,
    AuthoringSourceMark,
)
from .models import (
    AUTHORING_API_VERSION,
    AUTHORING_KIND,
    MAX_AUTHORING_BYTES,
    SemanticAssemblyAuthoring,
)
from .validation import validate_authoring

MAX_AUTHORING_EVENTS = 65_536
MAX_AUTHORING_NODES = 32_768
MAX_AUTHORING_DEPTH = 64
MAX_AUTHORING_SCALAR_CHARS = 4_096
MAX_AUTHORING_COLLECTION_ITEMS = 16_384
MAX_AUTHORING_ALIASES = 128
MAX_AUTHORING_EXPANDED_NODES = 65_536
MAX_AUTHORING_DIAGNOSTICS = 100

_JSON_BOOL = re.compile(r"^(?:true|false)$")
_JSON_NULL = re.compile(r"^(?:null)$")
_JSON_INT = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
_JSON_FLOAT = re.compile(
    r"^-?(?:(?:0|[1-9][0-9]*)\.[0-9]+(?:[eE][+-]?[0-9]+)?|"
    r"(?:0|[1-9][0-9]*)[eE][+-]?[0-9]+)$"
)
_ALLOWED_TAGS = {
    "tag:yaml.org,2002:map",
    "tag:yaml.org,2002:seq",
    "tag:yaml.org,2002:str",
    "tag:yaml.org,2002:null",
    "tag:yaml.org,2002:bool",
    "tag:yaml.org,2002:int",
    "tag:yaml.org,2002:float",
}
_NON_FINITE_SPELLINGS = {".nan", ".inf", "+.inf", "-.inf"}
_TYPED_SCALAR_PATTERNS = {
    "tag:yaml.org,2002:null": _JSON_NULL,
    "tag:yaml.org,2002:bool": _JSON_BOOL,
    "tag:yaml.org,2002:int": _JSON_INT,
    "tag:yaml.org,2002:float": _JSON_FLOAT,
}
_SAFE_PATH_PARTS = {
    "apiVersion",
    "kind",
    "metadata",
    "bundleId",
    "modelVersion",
    "description",
    "spec",
    "source",
    "sourceId",
    "catalogFingerprint",
    "entities",
    "entityId",
    "label",
    "fields",
    "fieldId",
    "dataType",
    "allowedAggregations",
    "valueSemantics",
    "relationships",
    "relationshipId",
    "targetEntityId",
    "sourceFields",
    "targetFields",
    "calculatedFields",
    "name",
    "expression",
    "outputType",
    "requires",
    "zeroDivisionPolicy",
    "measures",
    "measureId",
    "aggregation",
    "grains",
    "grainId",
    "attributes",
    "sourceReferences",
    "referenceId",
    "compatibility",
    "deploymentBindings",
    "bindingId",
    "environment",
    "connectionReference",
}


class _BoundedSafeLoader(yaml.SafeLoader):
    pass


_BoundedSafeLoader.yaml_implicit_resolvers = {}
_BoundedSafeLoader.add_implicit_resolver("tag:yaml.org,2002:bool", _JSON_BOOL, list("tf"))
_BoundedSafeLoader.add_implicit_resolver("tag:yaml.org,2002:null", _JSON_NULL, ["n"])
_BoundedSafeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int",
    _JSON_INT,
    list("-0123456789"),
)
_BoundedSafeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    _JSON_FLOAT,
    list("-0123456789"),
)


class _AuthoringLoadFailure(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        node: Node | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.node = node


def _mark(node: Node | None) -> AuthoringSourceMark | None:
    if node is None:
        return None
    return AuthoringSourceMark(line=node.start_mark.line + 1, column=node.start_mark.column + 1)


def _failure_result(failure: _AuthoringLoadFailure) -> AuthoringParseResult:
    diagnostic = AuthoringDiagnostic(
        code=failure.code,  # type: ignore[arg-type]
        message=failure.safe_message,
        mark=_mark(failure.node),
    )
    return AuthoringParseResult(diagnostics=(diagnostic,), issue_count=1)


def _scan_events(payload: str) -> None:
    node_count = 0
    alias_count = 0
    depth = 0
    for event_count, event in enumerate(
        yaml.parse(payload, Loader=_BoundedSafeLoader),
        start=1,
    ):
        if event_count > MAX_AUTHORING_EVENTS:
            raise _AuthoringLoadFailure("structure_limit", "The document has too many YAML events.")
        if isinstance(event, NodeEvent):
            node_count += 1
            if node_count > MAX_AUTHORING_NODES:
                raise _AuthoringLoadFailure(
                    "structure_limit", "The document has too many YAML nodes."
                )
        if isinstance(event, AliasEvent):
            alias_count += 1
            if alias_count > MAX_AUTHORING_ALIASES:
                raise _AuthoringLoadFailure("structure_limit", "The document has too many aliases.")
        if isinstance(event, CollectionStartEvent):
            depth += 1
            if depth > MAX_AUTHORING_DEPTH:
                raise _AuthoringLoadFailure("structure_limit", "The document is nested too deeply.")
        elif isinstance(event, CollectionEndEvent):
            depth -= 1


def _validate_node_graph(root: Node) -> dict[tuple[str | int, ...], AuthoringSourceMark]:
    marks: dict[tuple[str | int, ...], AuthoringSourceMark] = {}
    active: set[int] = set()
    expanded_nodes = 0

    def walk(node: Node, path: tuple[str | int, ...], depth: int) -> None:
        nonlocal expanded_nodes
        expanded_nodes += 1
        if expanded_nodes > MAX_AUTHORING_EXPANDED_NODES:
            raise _AuthoringLoadFailure(
                "structure_limit",
                "Alias expansion exceeds the document limit.",
                node=node,
            )
        if depth > MAX_AUTHORING_DEPTH:
            raise _AuthoringLoadFailure(
                "structure_limit", "The document is nested too deeply.", node=node
            )
        identity = id(node)
        if identity in active:
            raise _AuthoringLoadFailure(
                "unsupported_yaml", "Cyclic aliases are not supported.", node=node
            )
        if node.tag not in _ALLOWED_TAGS:
            raise _AuthoringLoadFailure(
                "unsupported_yaml", "Custom or unsupported YAML tags are not allowed.", node=node
            )
        marks[path] = _mark(node) or AuthoringSourceMark(line=1, column=1)
        if isinstance(node, ScalarNode):
            if len(node.value) > MAX_AUTHORING_SCALAR_CHARS:
                raise _AuthoringLoadFailure(
                    "structure_limit", "A scalar exceeds the character limit.", node=node
                )
            if node.value.lower() in _NON_FINITE_SPELLINGS:
                raise _AuthoringLoadFailure(
                    "unsupported_yaml", "Non-finite numbers are not supported.", node=node
                )
            scalar_pattern = _TYPED_SCALAR_PATTERNS.get(node.tag)
            if scalar_pattern is not None and scalar_pattern.fullmatch(node.value) is None:
                raise _AuthoringLoadFailure(
                    "unsupported_yaml",
                    "Typed scalars must use JSON-compatible syntax.",
                    node=node,
                )
            return

        active.add(identity)
        try:
            if isinstance(node, SequenceNode):
                if len(node.value) > MAX_AUTHORING_COLLECTION_ITEMS:
                    raise _AuthoringLoadFailure(
                        "structure_limit", "A sequence has too many items.", node=node
                    )
                for index, item in enumerate(node.value):
                    walk(item, (*path, index), depth + 1)
                return
            if isinstance(node, MappingNode):
                if len(node.value) > MAX_AUTHORING_COLLECTION_ITEMS:
                    raise _AuthoringLoadFailure(
                        "structure_limit", "A mapping has too many entries.", node=node
                    )
                keys: set[str] = set()
                for key_node, value_node in node.value:
                    if (
                        not isinstance(key_node, ScalarNode)
                        or key_node.tag != "tag:yaml.org,2002:str"
                    ):
                        raise _AuthoringLoadFailure(
                            "unsupported_yaml", "Mapping keys must be strings.", node=key_node
                        )
                    key = key_node.value
                    if key == "<<":
                        raise _AuthoringLoadFailure(
                            "unsupported_yaml", "YAML merge keys are not supported.", node=key_node
                        )
                    if key in keys:
                        raise _AuthoringLoadFailure(
                            "unsupported_yaml",
                            "Duplicate mapping keys are not supported.",
                            node=key_node,
                        )
                    keys.add(key)
                    walk(value_node, (*path, key), depth + 1)
                return
            raise _AuthoringLoadFailure(
                "unsupported_yaml", "Unsupported YAML node type.", node=node
            )
        finally:
            active.remove(identity)

    walk(root, (), 1)
    return marks


def _path(location: Iterable[str | int]) -> AuthoringPath:
    return AuthoringPath(
        parts=tuple(
            part if isinstance(part, int) or part in _SAFE_PATH_PARTS else "member"
            for part in location
        )
    )


def _validation_result(
    error: ValidationError,
    marks: dict[tuple[str | int, ...], AuthoringSourceMark],
) -> AuthoringParseResult:
    errors = error.errors()
    diagnostics: list[AuthoringDiagnostic] = []
    for item in errors[:MAX_AUTHORING_DIAGNOSTICS]:
        location = tuple(part for part in item["loc"] if isinstance(part, (str, int)))
        code = "invalid_member"
        if location and location[0] in {"apiVersion", "api_version", "kind"}:
            code = "incompatible_schema"
        elif any(
            part in {"description", "connectionReference", "connection_reference"}
            for part in location
        ):
            code = "unsafe_content"
        mark = marks.get(location)
        if mark is None:
            for size in range(len(location) - 1, -1, -1):
                mark = marks.get(location[:size])
                if mark is not None:
                    break
        diagnostics.append(
            AuthoringDiagnostic(
                code=code,  # type: ignore[arg-type]
                path=_path(location),
                mark=mark,
                message="This authoring member is not valid.",
            )
        )
    return AuthoringParseResult(
        diagnostics=tuple(diagnostics),
        issue_count=len(errors),
        truncated=len(errors) > MAX_AUTHORING_DIAGNOSTICS,
    )


class SemanticAssemblyAuthoringLoader:
    """Load untrusted YAML through bounded composition before construction."""

    def load(self, payload: str) -> AuthoringParseResult:
        try:
            byte_length = len(payload.encode("utf-8"))
        except UnicodeError:
            return _failure_result(
                _AuthoringLoadFailure("invalid_encoding", "The document is not valid UTF-8 text.")
            )
        if byte_length > MAX_AUTHORING_BYTES:
            return _failure_result(
                _AuthoringLoadFailure("input_too_large", "The document exceeds the byte limit.")
            )

        loader: _BoundedSafeLoader | None = None
        try:
            _scan_events(payload)
            loader = _BoundedSafeLoader(payload)
            root = loader.get_single_node()
            if root is None:
                raise _AuthoringLoadFailure(
                    "invalid_yaml", "The document must contain one mapping."
                )
            marks = _validate_node_graph(root)
            data: Any = loader.construct_document(root)
        except _AuthoringLoadFailure as failure:
            return _failure_result(failure)
        except (yaml.YAMLError, UnicodeError):
            return _failure_result(
                _AuthoringLoadFailure("invalid_yaml", "The document is not valid YAML or JSON.")
            )
        finally:
            if loader is not None:
                loader.dispose()

        if not isinstance(data, dict):
            return _failure_result(
                _AuthoringLoadFailure(
                    "invalid_yaml", "The authoring document must be a mapping.", node=root
                )
            )
        if data.get("apiVersion") != AUTHORING_API_VERSION or data.get("kind") != AUTHORING_KIND:
            return AuthoringParseResult(
                diagnostics=(
                    AuthoringDiagnostic(
                        code="incompatible_schema",
                        message="The authoring apiVersion or kind is not supported.",
                        mark=marks.get(()),
                    ),
                ),
                issue_count=1,
            )
        try:
            model = SemanticAssemblyAuthoring.model_validate(data)
        except ValidationError as error:
            return _validation_result(error, marks)
        semantic = validate_authoring(model, source_marks=marks)
        if not semantic.valid:
            return AuthoringParseResult(
                diagnostics=semantic.diagnostics,
                issue_count=semantic.issue_count,
                truncated=semantic.truncated,
            )
        return AuthoringParseResult(model=model)
