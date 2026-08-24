## Why

The library has a public `nl2data` package, but real composition still requires applications to import internal `nl2data_core` loaders, workflow runners, adapters, Memory providers, and governance objects. That makes the public API unclear and prevents the project from being consumed as a stable Python library.

P2.7 establishes a library-first application facade that owns composition and exposes stable request, workflow, clarification, cancellation, capability, and lifecycle contracts without making HTTP or a vendor framework part of the core package.

## What Changes

- Add a stable public Python facade under `nl2data` for configuration, engine construction, lifecycle, query submission, workflow handles, and protected outcomes.
- Provide a public composition/factory boundary so applications can configure AI provider, Memory, tenant context, state store, adapter, governance, and workflow runtime without importing internal implementation modules.
- Expose transport-neutral workflow status, clarification, cancellation, and capability contracts suitable for CLI, notebook, worker, or future HTTP hosting.
- Keep native adapters, model providers, Memory implementations, state stores, and governance internals behind typed ports and public factory/configuration inputs.
- Define synchronous convenience APIs only as explicit wrappers over the canonical async API, without blocking the event loop unexpectedly.
- Preserve stable lifecycle, protected result, safe error, tenant scope, idempotency, and durable recovery semantics.
- Add public import-boundary, compatibility, and end-to-end library usage tests.
- Keep HTTP hosting, authentication middleware, deployment probes, remote SDKs, LangGraph, and vendor-specific integrations outside this package.

## Capabilities

### New Capabilities

- `public-python-facade`: Stable library-facing application facade, composition, lifecycle, workflow handles, and async/sync query APIs.
- `transport-neutral-workflow-api`: Public workflow status, clarification, cancellation, capability, and protected event contracts independent of HTTP.
- `public-library-conformance`: Public import boundary, compatibility, safe serialization, and representative embedded-library usage tests.

### Modified Capabilities

- `public-models-and-errors`: Expand the stable public import boundary with facade and transport-neutral workflow contracts while preserving protected outcomes and safe errors.

## Impact

- Adds public facade/session/workflow modules under `src/nl2data/` and internal composition helpers under `src/nl2data_core/`.
- May reorganize existing `NL2DataEngine` construction while preserving current public behavior and P1/P2 fallback paths.
- Adds no FastAPI, ASGI, HTTP client, remote SDK, or vendor provider dependency.
- Adds public API compatibility, integration, security, and documentation tests.