## Context

The package already exposes `NL2DataEngine`, request/outcome models, and `load_config`, but real applications still need internal `nl2data_core` objects to compose AI, Memory, tenant scope, adapters, governance, and workflow runtime. The engine also exposes only a one-shot `query()` method, while later HTTP, CLI, notebook, and worker hosts need transport-neutral workflow handles and cancellation/status operations.

P2.7 makes the Python library boundary complete. It must preserve the core's dependency inversion: the public package may depend internally on core ports, but applications should not need to import internal implementations or transport frameworks.

## Goals / Non-Goals

**Goals:**

- Provide one public factory/facade for configuration and dependency composition.
- Expose stable async query, sync convenience, lifecycle, workflow status, cancellation, clarification, and capability contracts.
- Keep all returned data protected and transport-neutral.
- Preserve compatibility with existing `NL2DataEngine` and not-configured behavior.
- Make the facade usable in embedded Python applications without HTTP or vendor dependencies.

**Non-Goals:**

- HTTP routes, OpenAPI, SSE, remote SDKs, authentication middleware, or deployment manifests.
- Exposing internal adapter/provider/store classes as public API.
- Replacing the internal workflow runtime, Memory, tenant, governance, or adapter contracts.

## Decisions

1. **Use a public `NL2Data` application facade and factory.** The facade owns lifecycle and delegates execution to a configured runtime port. `NL2DataEngine` remains available for compatibility; new code uses the facade. A public constructor accepting every internal dependency was rejected because it would freeze implementation details into the library API.

2. **Define transport-neutral workflow handles and events.** A handle exposes workflow identity, current safe status, cancellation, and outcome retrieval. Events contain stage/status/fingerprint references only. HTTP adapters can map these models later without changing core contracts.

3. **Keep async canonical; provide explicit sync wrapper.** `aquery()`/`aexecute()` are primary. `query()` may use `asyncio.run()` only when no event loop is active and SHALL raise a clear error inside an active loop rather than silently blocking or nesting loops.

4. **Separate composition from authentication.** The facade accepts a trusted tenant/subject context and configured providers through typed composition inputs; it never authenticates users, trusts client tenant claims, or resolves secrets itself.

5. **Expose capabilities as safe snapshots.** Provider, adapter, Memory, workflow, and optional feature capabilities are represented by immutable identifiers and bounded flags. Native clients, credentials, policy internals, and provider objects never appear in public snapshots.

6. **Preserve safe fallback semantics.** A facade without a configured runtime returns the existing protected `NOT_CONFIGURED` outcome. Clarification remains a structured public outcome; cancellation and errors use stable public codes.

## Risks / Trade-offs

- [Risk] A public facade can accidentally re-export internals. → Enforce explicit `__all__`, import-boundary tests, and public models that contain no native types.
- [Risk] Sync wrappers can be misused from async applications. → Detect a running event loop and require the async method there.
- [Risk] Workflow handles may imply durable state. → Document that durability depends on the configured StateStore and handles expose references, not raw state.
- [Risk] Public API evolution becomes costly. → Version public models/contracts and keep provider-specific options in composition profiles rather than public method parameters.

## Migration Plan

1. Add public models, facade, factory, and workflow-handle contracts alongside the existing engine.
2. Route facade execution to the existing framework-neutral workflow runtime and preserve P1/P2 fallback behavior.
3. Add public import and embedded-library usage tests.
4. Deprecate direct application imports of `nl2data_core` in documentation without removing internal modules.
5. Add HTTP hosting as a separate package over the facade in a later change.

## Open Questions

- Should the facade be named `NL2Data` or `NL2DataClient` for embedded use?
- Should workflow status retrieval require a durable StateStore or return an in-process observation when none is configured?
- Which public options are stable enough to expose in P2.7 versus leaving to configuration profiles?