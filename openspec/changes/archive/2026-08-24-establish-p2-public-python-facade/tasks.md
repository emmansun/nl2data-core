## 1. Public Contracts

- [x] 1.1 Define public immutable workflow handle, workflow status, stage/event, cancellation request, capability, and safe usage-error models.
- [x] 1.2 Define stable public facade protocol with async query, sync convenience, initialize, drain, health, capabilities, workflow lookup, cancellation, and close operations.
- [x] 1.3 Extend public exports and error/status contracts without exposing internal runner, adapter, provider, store, or tenant claim types.
- [x] 1.4 Add public model contract tests for immutability, bounds, safe serialization, clarification, cancellation, and unknown-field rejection.

## 2. Composition and Facade

- [x] 2.1 Define typed composition profile/factory inputs for workflow runtime, AI provider, Memory, tenant context, adapter, governance, state store, and telemetry ports.
- [x] 2.2 Implement the public `NL2Data` facade delegating lifecycle and query execution to the configured workflow runtime.
- [x] 2.3 Preserve `NL2DataEngine` compatibility and the not-configured fallback while routing new composition through the facade boundary.
- [x] 2.4 Ensure facade close/drain operations are idempotent and safely close configured providers, adapters, Memory, and state resources.

## 3. Async/Sync Query API

- [x] 3.1 Implement canonical async query submission and protected outcome mapping.
- [x] 3.2 Implement sync convenience wrapper that runs only outside an active event loop and raises a stable usage error inside one.
- [x] 3.3 Implement workflow handle/status lookup and cancellation through transport-neutral runtime ports.
- [x] 3.4 Add embedded library integration tests using only `nl2data` public imports and deterministic providers.

## 4. Public Boundary Security and Compatibility

- [x] 4.1 Add import-boundary tests proving base library usage does not require HTTP, LangGraph, database drivers, vendor model SDKs, or native provider types.
- [x] 4.2 Add serialization tests proving public status, capability, clarification, cancellation, and outcomes contain only safe bounded fields and fingerprints.
- [x] 4.3 Add compatibility tests for existing P0/P1/P2 query behavior, lifecycle errors, tenant scope propagation, durable idempotency, and protected results.
- [x] 4.4 Add deprecation/documentation guidance for direct application imports from `nl2data_core` without removing internal modules.

## 5. Quality Gates and Future Hosting Boundary

- [x] 5.1 Run complete P0–P2.6 tests plus public facade, security, compatibility, Ruff, Mypy, and package-install checks.
- [x] 5.2 Document embedded library usage, async-first behavior, sync restrictions, workflow handles, and configured durability semantics.
- [x] 5.3 Define the integration boundary for a later `nl2data_http` package without adding HTTP dependencies to core.