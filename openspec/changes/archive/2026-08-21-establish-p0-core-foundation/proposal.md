## Why

NL2Data currently has the repository identity and design direction, but no implementation or executable contract foundation. Establishing the P0 contracts now creates a stable public Python boundary and a testable internal composition layer before database adapters, governance rules, providers, and transport integrations are added.

## What Changes

- Establish the project naming boundary: distribution `nl2data-core`, public import package `nl2data`, and internal implementation package `nl2data_core`.
- Define public, typed, immutable request, context, outcome, result, workflow, and capability models.
- Add structured, transport-neutral public errors with stable categories and error codes.
- Add strict configuration loading and validation for a versioned effective configuration snapshot.
- Define the single normative async-first `QueryAdapter` Protocol and its canonical artifact, capability, validation, and execution models.
- Establish the workflow state foundation with versioned states, statuses, events, valid transitions, budgets, and an in-memory state store.
- Establish plugin manifest, capability, permission, compatibility, and registry contracts without implementing arbitrary plugin execution.
- Establish vendor-neutral telemetry, audit, trace, metric, and structured-log interfaces with safe in-memory implementations where needed for tests.
- Add the `NL2DataEngine` lifecycle and composition skeleton; unsupported query execution must produce an explicit outcome or structured error rather than pretend to be implemented.
- Add focused unit and contract tests for public imports, configuration validation, adapter contracts, workflow transitions, plugin registration, error serialization, and engine lifecycle.

## Capabilities

### New Capabilities

- `public-models-and-errors`: Stable public Python models, import boundary, structured errors, and protected result contracts.
- `configuration-foundation`: Versioned configuration loading, strict validation, immutable effective snapshots, and configuration fingerprints.
- `query-adapter-contract`: The canonical async-first QueryAdapter Protocol and generic query artifact lifecycle models.
- `workflow-state-foundation`: Typed workflow state, statuses, events, transition validation, budgets, and in-memory state storage.
- `plugin-registry-foundation`: Plugin manifest and capability registry contracts with compatibility and permission validation.
- `telemetry-interfaces`: Vendor-neutral telemetry context, structured event, audit, trace, metric, and logging ports with redaction-safe boundaries.
- `engine-lifecycle`: NL2DataEngine creation, readiness, query boundary, health, drain, and close lifecycle skeleton.

### Modified Capabilities

<!-- No existing OpenSpec capabilities exist in this repository. -->

## Impact

- Adds the initial Python package layout under `src/nl2data/` and `src/nl2data_core/`.
- Adds Pydantic 2.x and YAML parsing as core dependencies, with Python 3.11+ as the target runtime.
- Establishes public APIs that future adapters, workflow implementations, plugin SDKs, and optional hosting packages must consume.
- Adds unit and contract test surfaces under `tests/`.
- Does not add database drivers, LLM SDKs, HTTP frameworks, telemetry backends, durable state stores, or real query adapters in P0.
- Resolves the distribution-name decision in favor of `nl2data-core`; any conflicting packaging design text must be updated in a later documentation alignment task before release.
