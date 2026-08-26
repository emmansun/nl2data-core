"""Deterministic fake MongoDB driver/collection executor.

The fake executor implements a small, strict subset of MongoDB read
semantics over in-memory documents: dotted-path resolution, allowlisted
comparison operators, inclusion projections, stable multi-key sorts,
skip/limit bounds, and the allowlisted aggregation stages.  Behavior is
fully deterministic - no wall-clock time, randomness, or JavaScript - so
conformance cases can assert exact rows and fingerprints.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .models import MongoExecutionError

_MISSING = object()


def _path_value(document: Mapping[str, Any], path: str) -> Any:
    """Resolve a canonical dotted path inside a document."""
    value: Any = document
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _matches(document: Mapping[str, Any], filter_: Mapping[str, Any]) -> bool:
    for path, expected in filter_.items():
        actual = _path_value(document, path)
        if isinstance(expected, Mapping) and any(
            str(key).startswith("$") for key in expected
        ):
            for operator, operand in expected.items():
                if not _compare(actual, operator, operand):
                    return False
        elif actual == _MISSING or actual != expected:
            return False
    return True


def _compare(actual: Any, operator: str, operand: Any) -> bool:
    if operator == "$eq":
        return bool(actual == operand)
    if operator == "$ne":
        return bool(actual != operand)
    if operator in {"$gt", "$gte", "$lt", "$lte"}:
        if actual is _MISSING or not isinstance(actual, (int, float, str)):
            return False
        if operator == "$gt":
            return bool(actual > operand)
        if operator == "$gte":
            return bool(actual >= operand)
        if operator == "$lt":
            return bool(actual < operand)
        return bool(actual <= operand)
    if operator == "$in":
        if not isinstance(operand, list):
            return False
        return bool(actual in operand)
    if operator == "$nin":
        if not isinstance(operand, list):
            return False
        return bool(actual not in operand)
    raise MongoExecutionError(
        f"fake executor does not support operator '{operator}'",
        details={"operator": operator},
    )


def _sort_documents(
    documents: list[dict[str, Any]], sort: Mapping[str, int]
) -> list[dict[str, Any]]:
    """Stable multi-key sort; repeated passes preserve earlier ordering."""
    ordered = documents
    for path, direction in reversed(list(sort.items())):
        ordered = sorted(
            ordered,
            key=lambda doc: (False, _path_value(doc, path))
            if _path_value(doc, path) is _MISSING
            else (True, _path_value(doc, path)),
            reverse=(direction == -1),
        )
    return ordered


def _project_document(
    document: Mapping[str, Any], projection: Mapping[str, Any]
) -> dict[str, Any]:
    if not projection:
        return {key: value for key, value in document.items() if key != "_id"}
    renames = {
        path: marker[1:]
        for path, marker in projection.items()
        if isinstance(marker, str) and marker.startswith("$")
    }
    inclusions = {path for path, marker in projection.items() if marker == 1}
    if inclusions or renames:
        result: dict[str, Any] = {}
        #: (length, path) ordering keeps nested builds deterministic.
        for path in sorted(inclusions | set(renames), key=lambda item: (len(item), item)):
            value = _path_value(document, renames.get(path, path))
            if value is _MISSING:
                continue
            parts = path.split(".")
            target = result
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value
        return result
    exclusions = set(projection)
    return {
        key: value
        for key, value in document.items()
        if key not in exclusions and key != "_id"
    }


def _group_documents(
    documents: list[dict[str, Any]], argument: Mapping[str, Any]
) -> list[dict[str, Any]]:
    groups: dict[Any, list[dict[str, Any]]] = {}
    order: list[Any] = []
    group_id = argument.get("_id")
    for document in documents:
        key: Any = None
        if isinstance(group_id, str):
            key = _path_value(document, group_id[1:])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(document)

    output: list[dict[str, Any]] = []
    for key in order:
        members = groups[key]
        row: dict[str, Any] = {"_id": key}
        for alias, expression in argument.items():
            if alias == "_id":
                continue
            expr_name, expr_value = next(iter(expression.items()))
            if isinstance(expr_value, int):
                if expr_name == "$sum":
                    row[alias] = len(members)
                else:
                    row[alias] = expr_value
                continue
            path = expr_value[1:]
            values: list[Any] = []
            for member in members:
                value = _path_value(member, path)
                if value is not _MISSING and isinstance(value, (int, float)):
                    values.append(value)
            if expr_name == "$sum":
                row[alias] = sum(values)
            elif expr_name == "$avg":
                row[alias] = sum(values) / len(values) if values else None
            elif expr_name == "$min":
                row[alias] = min(values) if values else None
            elif expr_name == "$max":
                row[alias] = max(values) if values else None
            else:
                raise MongoExecutionError(
                    f"fake executor does not support expression '{expr_name}'",
                    details={"expression": expr_name},
                )
        output.append(row)
    return output


def _unwind_documents(documents: list[dict[str, Any]], argument: Any) -> list[dict[str, Any]]:
    path = argument[1:] if isinstance(argument, str) else argument["path"][1:]
    expanded: list[dict[str, Any]] = []
    for document in documents:
        value = _path_value(document, path)
        if not isinstance(value, list):
            continue
        parts = path.split(".")
        for item in value:
            if not isinstance(item, Mapping):
                continue
            copy = dict(document)
            target = copy
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = dict(item)
            expanded.append(copy)
    return expanded


class FakeMongoExecutor:
    """In-memory executor with deterministic find/aggregate/count semantics.

    ``collections`` maps collection names to sequences of documents.
    Documents are copied on ingest so the executor never aliases caller
    state, and iteration order is preserved for deterministic results.
    """

    def __init__(
        self,
        collections: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    ) -> None:
        self._collections: dict[str, tuple[dict[str, Any], ...]] = {
            name: tuple(dict(document) for document in documents)
            for name, documents in (collections or {}).items()
        }
        self._closed = False

    def _require_open(self) -> None:
        if self._closed:
            raise MongoExecutionError(
                "fake executor is closed",
                details={"executor": "fake"},
            )

    def _documents(self, collection: str) -> tuple[dict[str, Any], ...]:
        self._require_open()
        try:
            return self._collections[collection]
        except KeyError as error:
            raise MongoExecutionError(
                f"collection '{collection}' does not exist",
                details={"collection": collection},
            ) from error

    def find_documents(
        self,
        *,
        collection: str,
        filter_: Mapping[str, Any],
        projection: Mapping[str, Any],
        sort: Mapping[str, int],
        skip: int | None,
        limit: int | None,
    ) -> tuple[dict[str, Any], ...]:
        matched = [
            document for document in self._documents(collection) if _matches(document, filter_)
        ]
        ordered = _sort_documents(matched, sort)
        start = skip or 0
        bounded = ordered[start:] if limit is None else ordered[start : start + limit]
        return tuple(
            _project_document(document, projection) for document in bounded
        )

    def aggregate_documents(
        self,
        *,
        collection: str,
        pipeline: tuple[Mapping[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        documents = list(self._documents(collection))
        for stage in pipeline:
            name, argument = next(iter(stage.items()))
            if name == "$match":
                documents = [
                    document for document in documents if _matches(document, argument)
                ]
            elif name == "$sort":
                documents = _sort_documents(documents, argument)
            elif name == "$skip":
                documents = documents[argument:]
            elif name == "$limit":
                documents = documents[:argument]
            elif name == "$project":
                documents = [
                    _project_document(document, argument) for document in documents
                ]
            elif name == "$group":
                documents = _group_documents(documents, argument)
            elif name == "$count":
                documents = [{argument: len(documents)}]
            elif name == "$unwind":
                documents = _unwind_documents(documents, argument)
            else:
                raise MongoExecutionError(
                    f"fake executor does not support stage '{name}'",
                    details={"stage": name},
                )
        return tuple(documents)

    def count_documents(
        self,
        *,
        collection: str,
        filter_: Mapping[str, Any],
    ) -> int:
        return sum(
            1 for document in self._documents(collection) if _matches(document, filter_)
        )

    def list_collections(self) -> tuple[str, ...]:
        self._require_open()
        return tuple(self._collections)

    def sample_document(self, collection: str) -> dict[str, Any] | None:
        documents = self._documents(collection)
        if not documents:
            return None
        return dict(documents[0])

    def available(self) -> bool:
        return not self._closed

    def close(self) -> None:
        self._closed = True
        self._collections = {}
