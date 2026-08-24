"""The MongoDB adapter: a specialization of the canonical QueryAdapter contract.

MongoDB-specific behavior (guarding, execution, metadata discovery) stays
in this package; the adapter itself exposes only the generic lifecycle:
capabilities, parse, validate, generate, estimate_cost, execute, close.
The PyMongo driver is optional and lazy - the fake profile needs no driver
and the base package never imports one.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

from nl2data_core.adapters.models import (
    AdapterCapabilities,
    AdapterLimits,
    AsyncMode,
    CostEstimate,
    ExecutionResult,
    GeneratedArtifact,
    ParsedArtifact,
    ValidatedArtifact,
    ValidationContext,
)

from .client import MongoClientHandle
from .execution import execute_mongo_spec
from .executor import MongoExecutor
from .fake import FakeMongoExecutor
from .models import (
    MongoAdapterConfig,
    MongoAdapterError,
    MongoParsedArtifact,
    MongoProfile,
    MongoQuerySpec,
)
from .normalize import mql_spec_fingerprint
from .pymongo_executor import PyMongoExecutor
from .validation import MongoGuardPolicy, assert_validated


class MongoQueryAdapter:
    """Read-only structured MQL adapter over the generic contract.

    Parse/validate are pure and side-effect free; execution offloads the
    synchronous driver to a worker thread.  The guard policy is built from
    the adapter configuration (allowlist, bounds, tenant profile) and the
    driver is only ever reached with a validated spec.
    """

    def __init__(
        self,
        *,
        config: MongoAdapterConfig | None = None,
        executor: MongoExecutor | None = None,
    ) -> None:
        self._config = config or MongoAdapterConfig()
        if executor is not None:
            self._executor = executor
        elif self._config.profile == MongoProfile.FAKE:
            self._executor = FakeMongoExecutor()
        elif self._config.uri is None or self._config.database is None:
            raise MongoAdapterError(
                "the pymongo profile requires a uri and database",
                details={"profile": self._config.profile.value},
            )
        else:
            self._executor = PyMongoExecutor(self._config.uri, self._config.database)
        self._handle = MongoClientHandle(self._executor)
        self._policy = MongoGuardPolicy(
            allowed_collections=self._config.allowed_collections,
            allowed_fields=self._config.allowed_fields,
            max_limit=self._config.max_limit,
            max_skip=self._config.max_skip,
            max_stages=self._config.max_stages,
            require_limit=self._config.require_limit,
            tenant_profile=self._config.tenant_profile,
            required_obligation_fingerprint=self._config.required_obligation_fingerprint,
        )
        #: artifact_id -> parsed artifact retained across the lifecycle.
        self._parsed_by_id: dict[str, MongoParsedArtifact] = {}
        #: artifact_id -> validated spec retained for execution.
        self._spec_by_id: dict[str, MongoQuerySpec] = {}

    @property
    def config(self) -> MongoAdapterConfig:
        """The immutable configuration bound to this adapter."""
        return self._config

    @property
    def guard_policy(self) -> MongoGuardPolicy:
        """The guard policy derived from the adapter configuration."""
        return self._policy

    @property
    def handle(self) -> MongoClientHandle:
        """The client lifecycle handle (adapter-internal boundary)."""
        return self._handle

    def capabilities(self) -> AdapterCapabilities:
        profile = self._config.profile
        features = {
            "read_only",
            "structured_mql",
            "no_javascript",
            "bounded_results",
            "allowlist_validation",
            "tenant_obligations",
            "aggregation",
            "ordering",
            "list_ops",
            "fake" if profile == MongoProfile.FAKE else "pymongo",
        }
        return AdapterCapabilities(
            adapter_type="mongodb",
            query_language="mql",
            async_mode=AsyncMode.THREAD_OFFLOAD,
            features=frozenset(features),
            limits=AdapterLimits(max_result_rows=self._config.max_rows),
        )

    def _parse_spec(self, query: str) -> MongoQuerySpec:
        """Strict JSON wire form -> typed spec; never shell text or JS."""
        try:
            payload = json.loads(query)
        except json.JSONDecodeError as error:
            raise MongoAdapterError(
                "query is not the strict JSON wire form of a MongoDB spec",
                details={"cause_type": type(error).__name__},
            ) from error
        try:
            return MongoQuerySpec.model_validate(payload)
        except Exception as error:
            raise MongoAdapterError(
                "query is not a valid structured MongoDB specification",
                details={"cause_type": type(error).__name__},
            ) from error

    def parse(self, query: str, context: ValidationContext) -> ParsedArtifact:
        spec = self._parse_spec(query)
        parsed = MongoParsedArtifact(
            artifact_id=f"mongo-{len(self._parsed_by_id) + 1}",
            fingerprint=mql_spec_fingerprint(spec),
            spec=spec,
        )
        self._parsed_by_id[parsed.artifact_id] = parsed
        return ParsedArtifact(
            artifact_id=parsed.artifact_id,
            fingerprint=parsed.fingerprint,
            parse_metadata={
                "operation": spec.operation.value,
                "collection": spec.collection,
            },
        )

    def _obligation_policy(self, context: ValidationContext) -> MongoGuardPolicy:
        """The guard policy with the context's mandatory obligations merged in."""
        if not context.required_obligation_fingerprints:
            return self._policy
        return replace(
            self._policy,
            required_obligation_fingerprints=(
                self._policy.required_obligation_fingerprints
                | context.required_obligation_fingerprints
            ),
        )

    def validate(self, artifact: ParsedArtifact, context: ValidationContext) -> ValidatedArtifact:
        parsed = self._parsed_by_id.get(artifact.artifact_id)
        if parsed is None:
            raise MongoAdapterError(
                "cannot validate an artifact that was not parsed by this adapter",
                details={"artifact_id": artifact.artifact_id},
            )
        if (
            self._config.snapshot_fingerprint is not None
            and context.snapshot_fingerprint is not None
            and context.snapshot_fingerprint != self._config.snapshot_fingerprint
        ):
            raise MongoAdapterError(
                "the metadata snapshot does not match the adapter's bound snapshot",
                details={"artifact_id": artifact.artifact_id},
            )
        guard = assert_validated(
            parsed.spec,
            self._obligation_policy(context),
            field_bindings=context.field_bindings,
        )
        self._spec_by_id[artifact.artifact_id] = parsed.spec
        return ValidatedArtifact(
            artifact_id=artifact.artifact_id,
            fingerprint=guard.fingerprint,
            snapshot_fingerprint=context.snapshot_fingerprint,
            validation_metadata={
                "operation": parsed.spec.operation.value,
                "collection": parsed.spec.collection,
                "limit": str(parsed.spec.limit or 0),
            },
            obligations_verified=guard.obligations_verified,
            bounded_rows=guard.bounded_rows,
        )

    async def generate(self, query: str, context: ValidationContext) -> GeneratedArtifact:
        spec = self._parse_spec(query)
        guard = assert_validated(
            spec, self._obligation_policy(context), field_bindings=context.field_bindings
        )
        parsed = MongoParsedArtifact(
            artifact_id=f"mongo-{len(self._parsed_by_id) + 1}",
            fingerprint=mql_spec_fingerprint(spec),
            spec=spec,
        )
        self._parsed_by_id[parsed.artifact_id] = parsed
        self._spec_by_id[parsed.artifact_id] = spec
        return GeneratedArtifact(
            artifact_id=parsed.artifact_id,
            fingerprint=guard.fingerprint,
            content_type="application/json",
            size_bytes=len(query.encode("utf-8")),
            metadata={"operation": spec.operation.value},
        )

    async def estimate_cost(
        self, artifact: ValidatedArtifact, context: ValidationContext
    ) -> CostEstimate:
        spec = self._spec_by_id.get(artifact.artifact_id)
        if spec is None:
            raise MongoAdapterError(
                "cannot estimate an artifact that was not validated by this adapter",
                details={"artifact_id": artifact.artifact_id},
            )
        return CostEstimate(estimated_units=spec.limit or self._config.max_rows)

    async def execute(
        self, artifact: ValidatedArtifact, context: ValidationContext
    ) -> ExecutionResult:
        spec = self._spec_by_id.get(artifact.artifact_id)
        if spec is None:
            raise MongoAdapterError(
                "cannot execute an artifact that was not validated by this adapter",
                details={"artifact_id": artifact.artifact_id},
            )
        return await asyncio.to_thread(
            self._execute_threaded,
            spec,
            max_rows=(
                context.limits.max_result_rows
                if context.limits is not None
                else self._config.max_rows
            ),
            max_result_bytes=context.max_result_bytes,
            timeout_seconds=context.execution_timeout_seconds or 30.0,
        )

    def _execute_threaded(
        self,
        spec: MongoQuerySpec,
        *,
        max_rows: int,
        max_result_bytes: int | None,
        timeout_seconds: float,
    ) -> ExecutionResult:
        self._handle.ensure_ready()
        return execute_mongo_spec(
            self._handle.executor,
            spec,
            max_rows=max_rows,
            max_result_bytes=max_result_bytes,
            timeout_seconds=timeout_seconds,
        )

    async def close(self) -> None:
        self._handle.close()
        self._parsed_by_id.clear()
        self._spec_by_id.clear()
