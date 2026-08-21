## 1. Project Setup and Package Boundaries

- [x] 1.1 Add `pyproject.toml` for the `nl2data-core` distribution with Python 3.11+ metadata, Pydantic 2.x, YAML support, and test tooling dependencies.
- [x] 1.2 Create the `src/nl2data/` public package with `__init__.py`, `py.typed`, and only the documented public import surface.
- [x] 1.3 Create the `src/nl2data_core/` internal package and narrow internal port modules without exposing implementation imports publicly.
- [x] 1.4 Add the initial test layout for unit, contract, integration, and security tests.

## 2. Public Models and Structured Errors

- [x] 2.1 Implement immutable public request, options, context, result, outcome, capability, health, and lifecycle models with strict extra-field validation. (public-models-and-errors)
- [x] 2.2 Implement structured public errors with stable categories, codes, retryability, safe details, and serialization redaction. (public-models-and-errors)
- [x] 2.3 Implement the public import exports and verify that importing `nl2data` does not require optional provider dependencies. (public-models-and-errors)
- [x] 2.4 Add model and error serialization tests covering immutability, invalid fields, safe details, and protected result boundaries. (public-models-and-errors)

## 3. Configuration Foundation

- [x] 3.1 Implement strict versioned configuration models for service identity, runtime settings, and extension-safe sections. (configuration-foundation)
- [x] 3.2 Implement mapping and YAML loading into an immutable effective configuration snapshot with deterministic canonicalization and fingerprinting. (configuration-foundation)
- [x] 3.3 Implement secret-reference representation and safe diagnostic serialization without plaintext secret emission. (configuration-foundation)
- [x] 3.4 Reject unsupported schema versions, unknown strict fields, malformed values, and protected overrides before activation. (configuration-foundation)
- [x] 3.5 Add configuration tests for defaults, canonical fingerprints, immutability, secret redaction, and fail-closed validation. (configuration-foundation)

## 4. Query Adapter Contract

- [x] 4.1 Implement the canonical generic adapter models and enums from DDS-002, including `AsyncMode`, capabilities, artifacts, validation context, limits, and execution results. (query-adapter-contract)
- [x] 4.2 Define the single async-first `QueryAdapter` Protocol with synchronous pure parse/validate methods and asynchronous I/O boundaries. (query-adapter-contract)
- [x] 4.3 Implement safe artifact canonicalization and `sha256:<lowercase hexadecimal digest>` fingerprint generation without secrets. (query-adapter-contract)
- [x] 4.4 Add contract tests for protocol shape, artifact lifecycle, async mode declarations, and stable fingerprints. (query-adapter-contract)

## 5. Workflow State Foundation

- [x] 5.1 Implement versioned immutable workflow request, status, state, event, transition, and budget models. (workflow-state-foundation)
- [x] 5.2 Implement the allowed transition table and structured rejection of invalid or terminal-state transitions. (workflow-state-foundation)
- [x] 5.3 Implement the replaceable state-store Protocol and deterministic in-memory implementation with workflow ID lookup. (workflow-state-foundation)
- [x] 5.4 Enforce non-negative bounded attempts and event budgets and prevent unsafe raw payloads in state/event serialization. (workflow-state-foundation)
- [x] 5.5 Add transition, budget, serialization, and state-store contract tests. (workflow-state-foundation)

## 6. Plugin Manifest and Registry Foundation

- [x] 6.1 Implement immutable plugin identity, manifest, capability, permission, compatibility, and descriptor models. (plugin-registry-foundation)
- [x] 6.2 Implement manifest validation for required fields, version ranges, categories, permissions, and content digest format. (plugin-registry-foundation)
- [x] 6.3 Implement declarative plugin registration, capability/version resolution, and immutable registry generations without entry-point invocation. (plugin-registry-foundation)
- [x] 6.4 Add registry tests for invalid manifests, immutable descriptors, incompatible capability resolution, and non-execution of entry points. (plugin-registry-foundation)

## 7. Telemetry and Audit Interfaces

- [x] 7.1 Implement typed telemetry context, structured log, span, metric, and audit event models with opaque IDs and optional evidence fingerprints. (telemetry-interfaces)
- [x] 7.2 Define vendor-neutral telemetry and audit ports plus bounded in-memory sinks for deterministic tests. (telemetry-interfaces)
- [x] 7.3 Implement default safe-profile filtering for secrets, raw queries, raw results, unrestricted prompts, and unbounded attributes. (telemetry-interfaces)
- [x] 7.4 Define bounded sink-failure behavior that reports degradation without changing authorization or error outcomes. (telemetry-interfaces)
- [x] 7.5 Add telemetry tests for correlation, redaction, bounded attributes, sink behavior, and authorization independence. (telemetry-interfaces)

## 8. NL2DataEngine Lifecycle Skeleton

- [x] 8.1 Define the internal workflow execution port used by the engine and a not-configured implementation for P0. (engine-lifecycle)
- [x] 8.2 Implement `NL2DataEngine` creation, initialization, ready, draining, closed, health, and idempotent close behavior. (engine-lifecycle)
- [x] 8.3 Bind an immutable configuration snapshot and plugin registry generation to the engine lifecycle. (engine-lifecycle)
- [x] 8.4 Route public query requests only through the workflow port and return an explicit not-configured outcome when no executable workflow exists. (engine-lifecycle)
- [x] 8.5 Add lifecycle tests for readiness gating, public capability snapshots, query routing, drain rejection, and repeated close. (engine-lifecycle)

## 9. Integration, Documentation, and Validation

- [x] 9.1 Add a minimal README usage example showing the public package name and the explicit P0 not-configured behavior.
- [x] 9.2 Add architecture/import-boundary tests preventing public modules from importing optional database, LLM, HTTP, or telemetry backend dependencies.
- [x] 9.3 Run the full unit and contract test suite and type/lint checks for the new package surface.
- [x] 9.4 Align packaging documentation with the chosen `nl2data-core` distribution name and `nl2data` import name before release.
