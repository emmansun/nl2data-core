## MODIFIED Requirements

### Requirement: Immutable backend-neutral semantic query representation
The system SHALL represent an executable analytical request as an immutable, versioned `SemanticQueryIR` containing source identity, root entity, semantic selections, bounded filters, grouping, ordering, limit, time context, result shape, provenance, and capability requirements without embedding SQL syntax, MQL syntax, credentials, or driver-native values.

#### Scenario: IR can be fingerprinted deterministically
- **WHEN** equivalent IR inputs are canonicalized in different mapping insertion orders
- **THEN** they produce the same IR fingerprint and canonical serialization

#### Scenario: Physical syntax is rejected from the semantic contract
- **WHEN** an IR includes raw SQL text, MQL text, SQL AST nodes, credentials, or driver-native values
- **THEN** IR validation rejects the input before compilation

### Requirement: IR invariants are validated before compilation
The planner SHALL reject IR values with missing source identity, unbounded limits where bounded results are required, invalid operators or aggregation/grouping combinations, unsupported capabilities, unresolved provenance, or references outside the authorized semantic view.

#### Scenario: Invalid IR does not reach an adapter
- **WHEN** an IR references an unavailable semantic member or unsupported capability
- **THEN** compilation is rejected with a structured validation error and the adapter is not invoked

### Requirement: Planning components use IR directly
Intent builders, plan resolvers, workflow runtimes, evaluation components, and backend compilers SHALL consume validated `SemanticQueryIR` values directly and SHALL NOT construct or translate a legacy Semantic Query Plan.

#### Scenario: Intent produces canonical IR
- **WHEN** validated structured intent is converted into a logical query
- **THEN** the result is a `SemanticQueryIR` and is validated exactly once before compilation

#### Scenario: Compiler receives IR
- **WHEN** SQL or MongoDB compilation begins
- **THEN** the compiler receives the validated IR plus physical compilation context and does not require a legacy plan model
