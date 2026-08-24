"""Structured MongoDB adapter specialization (read-only, allowlist driven).

The base package imports no optional driver: PyMongo is loaded lazily by
the ``pymongo`` profile only, so every export here is safe to import
without the ``mongodb`` extra.
"""

from __future__ import annotations

from nl2data_core.adapters.mongodb.adapter import MongoQueryAdapter
from nl2data_core.adapters.mongodb.client import MongoClientHandle
from nl2data_core.adapters.mongodb.compile import MongoCompileError, compile_mongo_ir
from nl2data_core.adapters.mongodb.execution import (
    execute_mongo_spec,
    normalize_bson_cell,
)
from nl2data_core.adapters.mongodb.executor import MongoExecutor
from nl2data_core.adapters.mongodb.facts import extract_query_facts, facts_to_governance
from nl2data_core.adapters.mongodb.fake import FakeMongoExecutor
from nl2data_core.adapters.mongodb.metadata import discover_metadata
from nl2data_core.adapters.mongodb.models import (
    MongoAdapterConfig,
    MongoAdapterError,
    MongoCapabilityProfile,
    MongoExecutionError,
    MongoGuardResult,
    MongoMetadataSnapshot,
    MongoOperation,
    MongoParsedArtifact,
    MongoProfile,
    MongoQueryFacts,
    MongoQuerySpec,
    MongoUnavailableError,
    RoutingEvidence,
    RoutingKind,
    TenantObligation,
    mongo_spec_json,
)
from nl2data_core.adapters.mongodb.normalize import (
    assert_json_compatible,
    mql_spec_fingerprint,
    mql_spec_payload,
    normalize_mql_value,
    predicate_fingerprint,
)
from nl2data_core.adapters.mongodb.pymongo_executor import PyMongoExecutor
from nl2data_core.adapters.mongodb.validation import (
    DEFAULT_EXPRESSIONS,
    DEFAULT_OPERATORS,
    DEFAULT_STAGES,
    MongoGuardPolicy,
    assert_validated,
    run_guard,
)

__all__ = [
    "DEFAULT_EXPRESSIONS",
    "DEFAULT_OPERATORS",
    "DEFAULT_STAGES",
    "FakeMongoExecutor",
    "MongoAdapterConfig",
    "MongoAdapterError",
    "MongoCapabilityProfile",
    "MongoClientHandle",
    "MongoCompileError",
    "MongoExecutionError",
    "MongoExecutor",
    "MongoGuardPolicy",
    "MongoGuardResult",
    "MongoMetadataSnapshot",
    "MongoOperation",
    "MongoParsedArtifact",
    "MongoProfile",
    "MongoQueryAdapter",
    "MongoQueryFacts",
    "MongoQuerySpec",
    "MongoUnavailableError",
    "PyMongoExecutor",
    "RoutingEvidence",
    "RoutingKind",
    "TenantObligation",
    "assert_json_compatible",
    "assert_validated",
    "compile_mongo_ir",
    "discover_metadata",
    "execute_mongo_spec",
    "extract_query_facts",
    "facts_to_governance",
    "mongo_spec_json",
    "mql_spec_fingerprint",
    "mql_spec_payload",
    "normalize_bson_cell",
    "normalize_mql_value",
    "predicate_fingerprint",
    "run_guard",
]
