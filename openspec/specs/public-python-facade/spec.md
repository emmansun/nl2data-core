## Purpose

Define the public Python facade through which library users compose the system.

## Requirements

### Requirement: Library users can compose through the public package
The library SHALL provide a stable public facade/factory that composes configured workflow runtime, AI provider, Memory, tenant context, adapter, governance, and state-store capabilities without requiring application code to import `nl2data_core` implementation modules.

#### Scenario: Embedded application uses only public imports
- **WHEN** an application constructs and runs the library using `nl2data` public imports
- **THEN** it can initialize, query, inspect health/capabilities, and close without importing internal implementation classes

### Requirement: Async query is the canonical facade operation
The public facade SHALL provide an asynchronous query operation that routes through the governed workflow runtime and returns only protected `QueryOutcome` values.

#### Scenario: Unconfigured facade fails safely
- **WHEN** the facade has no configured executable runtime
- **THEN** the async query returns the stable not-configured outcome without invoking a native provider or adapter

### Requirement: Sync convenience does not corrupt event-loop behavior
The facade SHALL provide an explicit synchronous convenience operation only outside an active event loop and SHALL require callers inside an active loop to use the async operation.

#### Scenario: Sync call inside an event loop is rejected clearly
- **WHEN** a synchronous facade method is called from a running event loop
- **THEN** it raises a stable usage error without nesting or blocking the loop

### Requirement: Lifecycle is explicit and idempotent
The public facade SHALL expose initialization, draining, health, and close operations with the existing lifecycle states and idempotent close behavior.

#### Scenario: Close can be repeated safely
- **WHEN** an initialized facade is closed more than once
- **THEN** all close calls complete safely and the facade remains closed
