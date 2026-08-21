## Context

The repository currently contains only project metadata and an empty `src/` tree. The external DDS documents define a layered, hexagonal architecture for NL2Data, with `nl2data` as the public import package and `nl2data_core` as the internal implementation package. P0 must establish contracts that later adapters, providers, governance modules, and transports can consume without leaking implementation details.

The change crosses public API models, configuration, adapter ports, workflow state, plugin registration, telemetry, and engine lifecycle. The implementation target is Python 3.11+ with Pydantic 2.x. The base distribution is named `nl2data-core`; database drivers, LLM SDKs, HTTP frameworks, telemetry exporters, and durable stores remain optional.

## Goals / Non-Goals

**Goals:**

- Establish a stable public import boundary and typed immutable models.
- Provide strict configuration compilation into an immutable effective snapshot.
- Implement the single DDS-002 async-first QueryAdapter contract.
- Make workflow state and transition validity explicit and testable.
- Register plugins through validated manifests and capabilities without arbitrary execution.
- Define safe, vendor-neutral telemetry and audit ports.
- Provide an engine lifecycle skeleton that fails explicitly when runtime capabilities are not configured.
- Keep P0 small enough to run with in-memory test implementations.

**Non-Goals:**

- Real SQL, MongoDB, LLM, memory, governance, tenant, or HTTP implementations.
- Durable workflow persistence or exactly-once execution.
- Isolated-process or remote plugin runners.
- OpenTelemetry, Prometheus, cloud audit, or commercial FinOps integrations.
- Plugin installation, signing, SBOM verification, or vulnerability scanning.
- Full answer generation, policy enforcement, result masking, or query execution.

## Decisions

### 1. Use two import layers

The distribution metadata will identify `nl2data-core`, public applications will import from `nl2data`, and internal implementations will live under `nl2data_core`. Public modules will depend on narrow internal ports rather than exposing internal models. This follows DDS-015 and prevents future adapters from becoming accidental public API.

Alternative considered: placing all implementation under `nl2data`. Rejected because the design explicitly separates stable public contracts from replaceable internals.

### 2. Use Pydantic 2 models for serializable contracts

Public models, configuration models, adapter artifacts, workflow events, plugin manifests, and telemetry records will use frozen Pydantic models with `extra="forbid"` unless an explicit extension mapping is part of the contract. Protocols remain Python `Protocol` definitions for behavior. This provides validation, safe serialization, and consistent error reporting.

Alternative considered: dataclasses plus handwritten validation. Rejected because the P0 surface has many nested external-input contracts and needs uniform validation now.

### 3. Keep the adapter contract generic and async-first

The implementation will expose exactly one `QueryAdapter` Protocol with async I/O methods and synchronous pure parsing/validation methods. Canonical names are `adapter_type`, `query_language`, `AsyncMode`, and `artifact_fingerprint`. No SQL- or MongoDB-specific type appears in the core contract.

Alternative considered: separate synchronous and asynchronous adapter interfaces. Rejected because it duplicates compatibility surface and conflicts with DDS-002.

### 4. Treat configuration as a compiled snapshot

The loader will parse a mapping or YAML document, validate schema version and typed fields, apply only defined defaults, and produce an immutable `EffectiveConfig` with a deterministic fingerprint. Secret resolution and remote secret providers are represented as extension points; plaintext secrets are not emitted by serialization or fingerprints.

Alternative considered: passing mutable dictionaries through the engine. Rejected because request reproducibility and configuration safety require a bound snapshot.

### 5. Use an explicit workflow state machine

Workflow state will contain a version, status, request/workflow identifiers, bounded attempt counters, and references to evidence fingerprints. A transition function will reject invalid moves. The P0 state store is in-memory and concurrency-safe enough for tests; durable checkpointing is deferred.

Alternative considered: a free-form dictionary updated by engine nodes. Rejected because it makes stale execution and invalid gate bypasses difficult to detect.

### 6. Make plugin registration declarative

P0 validates plugin identity, manifest version, categories, capabilities, permissions, compatibility ranges, and content digest shape, then stores immutable descriptors in a registry. It does not import or execute plugin code. Resolution is explicit by plugin ID and capability.

Alternative considered: Python entry-point discovery during engine creation. Rejected for P0 because discovery and execution add supply-chain and lifecycle behavior that require their own design and tests.

### 7. Define telemetry as ports with safe in-memory sinks

Telemetry records will use opaque IDs, bounded attributes, classifications, and optional fingerprints. The interfaces will support logs, spans, metrics, and audit events while rejecting or omitting unsafe raw payloads by default. In-memory implementations make contract tests deterministic without selecting a vendor backend.

Alternative considered: direct OpenTelemetry dependency in the base package. Rejected because the base distribution must remain provider-neutral.

### 8. Keep the engine honest

`NL2DataEngine` will have explicit `created`, `initializing`, `ready`, `draining`, and `closed` lifecycle states. Query submission validates lifecycle and public inputs, then delegates to a workflow port. If no executable workflow is configured, it returns a stable not-configured outcome rather than fabricating a result.

Alternative considered: implementing a placeholder successful query. Rejected because it would make smoke tests misleading and establish unsafe semantics.

## Risks / Trade-offs

- [Risk] The initial contract set may evolve as real adapters are implemented → [Mitigation] Keep public models frozen, version contracts explicitly, and cover them with contract tests before adding providers.
- [Risk] Pydantic models can accidentally expose sensitive values through serialization → [Mitigation] Use explicit safe dump methods, secret-reference types, forbidden extra fields, and tests for redaction.
- [Risk] An in-memory state store does not survive process restart → [Mitigation] Mark durability out of scope and keep the state-store Protocol replaceable for a later change.
- [Risk] Declarative plugin registration delays extension discovery → [Mitigation] Implement a narrow registry now and add verified entry-point discovery as a separate capability.
- [Risk] A public `nl2data` package may import internal modules too directly → [Mitigation] Test the top-level import boundary and expose only selected symbols from `__init__.py`.
- [Risk] Distribution naming may conflict with earlier DDS-018 text → [Mitigation] Treat `nl2data-core` as the P0 packaging decision and align DDS-018 before publishing.
