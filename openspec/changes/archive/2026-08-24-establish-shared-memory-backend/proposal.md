## Why

The current MemoryProvider is process-local, so Memory context is lost or diverges when a library host runs more than one worker or Kubernetes Pod. A shared backend is needed to make bounded conversational context available across replicas while preserving the existing fail-closed scope rules and stateless degradation behavior.

## What Changes

- Add a Redis-backed implementation of the existing synchronous `MemoryProvider` contract.
- Add an optional Redis dependency/profile; the base package remains free of Redis imports.
- Persist only validated, safe `MemoryRecord` representations with schema/version metadata.
- Provide atomic append, compare-and-set, expiry, deletion, compaction, bounded recall, and provider availability checks across processes.
- Preserve tenant, session, conversation, adapter, and source scope isolation in shared keys and recall filtering.
- Normalize connection, timeout, serialization, capacity, and backend-unavailable failures through existing Memory error semantics.
- Add conformance, security, and optional real-Redis integration coverage without making Redis a mandatory test service.

## Capabilities

### New Capabilities

- `shared-memory-backend`: Shared, Redis-backed MemoryProvider behavior for multi-process and multi-Pod library hosts.

### Modified Capabilities

- None.

## Impact

Affected areas include `src/nl2data_core/memory`, optional dependency metadata in `pyproject.toml`, public composition/configuration wiring, and Memory tests. The implementation introduces an optional Redis client dependency and requires operators to provide a shared Redis-compatible service when cross-replica Memory is enabled. No HTTP hosting, authentication middleware, deployment manifests, or workflow state backend changes are included.
