## MODIFIED Requirements

### Requirement: Public import boundary is enforced
The conformance suite SHALL verify that documented library usage imports only `nl2data` public symbols and does not require HTTP, database-driver, model-provider, or workflow-framework dependencies. Documentation examples SHALL follow the same public import boundary, while internal implementation guides SHALL label `nl2data_core` imports as contributor-only.

#### Scenario: Optional dependencies remain unloaded
- **WHEN** the base library is imported and a facade is constructed
- **THEN** optional transport, provider, and database modules remain unloaded

#### Scenario: User documentation uses public imports
- **WHEN** a reader copies a getting-started example
- **THEN** it imports the supported public API and does not require internal modules

### Requirement: Public behavior is compatibility-tested
The conformance suite SHALL cover lifecycle, async/sync query behavior, not-configured fallback, protected outcomes, clarification, cancellation, capability snapshots, and idempotent close. Documentation SHALL identify which examples are deterministic, service-backed, or live-provider profiles.

#### Scenario: Public conformance is repeatable
- **WHEN** the same deterministic facade composition is exercised twice
- **THEN** protected outcomes and safe status evidence are equivalent

#### Scenario: Documentation labels optional profiles
- **WHEN** a guide requires PostgreSQL, Redis, MongoDB, or live AI access
- **THEN** it states the prerequisite and distinguishes skipped/unavailable verification from a verified result
