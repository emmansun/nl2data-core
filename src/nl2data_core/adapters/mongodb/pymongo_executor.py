"""Optional PyMongo executor: validated spec to controlled driver calls.

The driver is loaded lazily through :func:`importlib.import_module` so the
base package never imports PyMongo.  Driver calls are built only from the
validated structured spec - filter, projection, sort, skip, limit, and the
allowlisted pipeline - and the raw cursor never crosses this boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module
from importlib.util import find_spec
from typing import Any, cast

from .models import MongoExecutionError, MongoUnavailableError


def _driver_value(value: Any) -> Any:
    """Materialize immutable spec containers into driver-owned BSON values."""
    if isinstance(value, Mapping):
        return {key: _driver_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_driver_value(item) for item in value]
    return value


class PyMongoExecutor:
    """Sync PyMongo executor; the async adapter offloads calls to a thread.

    Connection is lazy: the first ``available()`` (or driver call) performs
    the import and a bounded ping readiness check.  A missing driver or an
    unreachable service raises :class:`MongoUnavailableError` - never a
    false conformance pass.
    """

    def __init__(
        self,
        uri: str,
        database: str,
        *,
        server_selection_timeout_ms: int = 3_000,
    ) -> None:
        self._uri = uri
        self._database = database
        self._timeout_ms = server_selection_timeout_ms
        self._client: Any = None
        self._closed = False

    @staticmethod
    def driver_available() -> bool:
        """Whether the optional ``pymongo`` driver is installed."""
        return find_spec("pymongo") is not None

    def _connect(self) -> Any:
        if self._client is not None:
            return self._client
        if self._closed:
            raise MongoUnavailableError(
                "the mongodb executor is closed",
                details={"database": self._database},
            )
        if not self.driver_available():
            raise MongoUnavailableError(
                "the pymongo driver is not installed; install the 'mongodb' extra",
                details={"cause_type": "ImportError"},
            )
        try:
            pymongo = cast(Any, import_module("pymongo"))
        except ImportError as error:
            raise MongoUnavailableError(
                "the pymongo driver is not installed; install the 'mongodb' extra",
                details={"cause_type": type(error).__name__},
            ) from error
        try:
            client = pymongo.MongoClient(
                self._uri, serverSelectionTimeoutMS=self._timeout_ms
            )
            client.admin.command("ping")
        except Exception as error:
            raise MongoUnavailableError(
                "the mongodb service is unavailable",
                details={"cause_type": type(error).__name__},
            ) from error
        self._client = client
        return client

    def _collection(self, collection: str) -> Any:
        client = self._connect()
        return client[self._database][collection]

    @staticmethod
    def _split_projection(
        projection: Mapping[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """Split a validated projection into driver inclusions and renames.

        Modern MongoDB ``find()`` rejects the ``$"field"`` rename syntax,
        so bounded renames are executed as inclusions of the target paths
        and applied client-side - the wire semantics stay driver-neutral.
        """
        renames = {
            output: marker[1:]
            for output, marker in projection.items()
            if isinstance(marker, str) and marker.startswith("$")
        }
        if not renames:
            return dict(projection) or None, {}
        inclusions: dict[str, Any] = {target: 1 for target in renames.values()}
        inclusions["_id"] = 0
        return inclusions, renames

    @staticmethod
    def _apply_renames(
        document: dict[str, Any], renames: dict[str, str]
    ) -> dict[str, Any]:
        renamed: dict[str, Any] = {}
        for output, target in renames.items():
            value: Any = document
            for part in target.split("."):
                if not isinstance(value, dict) or part not in value:
                    value = None
                    break
                value = value[part]
            renamed[output] = value
        return renamed

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
        try:
            driver_projection, renames = self._split_projection(projection)
            find_kwargs: dict[str, Any] = {
                "projection": driver_projection,
                "sort": list(sort.items()) or None,
            }
            if skip is not None:
                find_kwargs["skip"] = skip
            if limit is not None:
                find_kwargs["limit"] = limit
            cursor = self._collection(collection).find(_driver_value(filter_), **find_kwargs)
            documents = tuple(dict(document) for document in cursor)
            if renames:
                documents = tuple(
                    self._apply_renames(document, renames) for document in documents
                )
            return documents
        except MongoUnavailableError:
            raise
        except Exception as error:
            raise MongoExecutionError(
                "mongodb find failed",
                details={
                    "collection": collection,
                    "cause_type": type(error).__name__,
                },
            ) from error

    def aggregate_documents(
        self,
        *,
        collection: str,
        pipeline: tuple[Mapping[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        try:
            cursor = self._collection(collection).aggregate(_driver_value(pipeline))
            return tuple(dict(document) for document in cursor)
        except MongoUnavailableError:
            raise
        except Exception as error:
            raise MongoExecutionError(
                "mongodb aggregate failed",
                details={
                    "collection": collection,
                    "cause_type": type(error).__name__,
                },
            ) from error

    def count_documents(
        self,
        *,
        collection: str,
        filter_: Mapping[str, Any],
    ) -> int:
        try:
            count = self._collection(collection).count_documents(_driver_value(filter_))
            return int(count)
        except MongoUnavailableError:
            raise
        except Exception as error:
            raise MongoExecutionError(
                "mongodb count_documents failed",
                details={
                    "collection": collection,
                    "cause_type": type(error).__name__,
                },
            ) from error

    def list_collections(self) -> tuple[str, ...]:
        try:
            return tuple(self._connect()[self._database].list_collection_names())
        except MongoUnavailableError:
            raise
        except Exception as error:
            raise MongoExecutionError(
                "mongodb collection listing failed",
                details={"cause_type": type(error).__name__},
            ) from error

    def sample_document(self, collection: str) -> dict[str, Any] | None:
        """Read one deterministic sample document (lowest ``_id``) so
        repeated observations of an unchanged collection stay comparable."""
        try:
            document = self._collection(collection).find_one(sort=[("_id", 1)])
        except MongoUnavailableError:
            raise
        except Exception as error:
            raise MongoExecutionError(
                "mongodb sample document read failed",
                details={
                    "collection": collection,
                    "cause_type": type(error).__name__,
                },
            ) from error
        return dict(document) if document is not None else None

    def available(self) -> bool:
        if self._closed or not self.driver_available():
            return False
        try:
            self._connect()
            return True
        except MongoUnavailableError:
            return False

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        self._closed = True
