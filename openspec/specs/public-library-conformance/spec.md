## ADDED Requirements

### Requirement: Public import boundary is enforced
The conformance suite SHALL verify that documented library usage imports only `nl2data` public symbols and does not require HTTP, database-driver, model-provider, or workflow-framework dependencies.

#### Scenario: Optional dependencies remain unloaded
- **WHEN** the base library is imported and a facade is constructed
- **THEN** optional transport, provider, and database modules remain unloaded

### Requirement: Public behavior is compatibility-tested
The conformance suite SHALL cover lifecycle, async/sync query behavior, not-configured fallback, protected outcomes, clarification, cancellation, capability snapshots, and idempotent close.

#### Scenario: Public conformance is repeatable
- **WHEN** the same deterministic facade composition is exercised twice
- **THEN** protected outcomes and safe status evidence are equivalent
