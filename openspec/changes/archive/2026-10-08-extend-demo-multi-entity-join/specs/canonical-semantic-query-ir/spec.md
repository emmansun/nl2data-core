## ADDED Requirements

### Requirement: Canonical IR represents bounded computed query operations
The canonical `SemanticQueryIR` SHALL represent computed selections and derived aggregate stages using immutable, backend-neutral typed nodes. The closed operator set SHALL cover the required distinct aggregate with an optional filter, conditional expressions, `COALESCE`, `NULLIF`, bounded arithmetic and rounding, declared `DATE_TRUNC` buckets, and a bounded prior-period comparison. Nodes SHALL reference semantic fields only, obey configured depth and size bounds, participate in canonical fingerprints, and declare the capabilities required by consumers. Raw SQL, arbitrary CTE text, and arbitrary window specifications SHALL remain invalid.

#### Scenario: Target aggregate operations are represented without SQL text
- **WHEN** an IR represents a filtered distinct count, a `CASE WHEN` count, a coalesced value, or a date-bucketed prior-period metric using supported typed nodes
- **THEN** validation SHALL accept the IR only when all referenced fields belong to its current authorized view and the required capabilities are declared

#### Scenario: Prior-period comparison is limited to a declared bucket
- **WHEN** an IR requests a previous-period value for a supported date bucket and bounded ordering
- **THEN** the IR SHALL encode that operation structurally and SHALL reject arbitrary frame clauses, offsets, or executable expressions

#### Scenario: Unsupported or unbounded expression fails closed
- **WHEN** an IR contains raw SQL, an unknown operator, an out-of-view field, or an expression exceeding configured bounds
- **THEN** validation SHALL return a structured failure and no compiler or adapter SHALL be invoked

### Requirement: New optional IR operations preserve legacy identity
When an IR does not use the new expression or derived-stage members, its canonical payload and fingerprint SHALL remain byte-identical to the existing representation. New members SHALL be omitted from canonical payloads when unset.

#### Scenario: Existing single-entity IR keeps its fingerprint
- **WHEN** an existing IR is constructed without any new computed-query member
- **THEN** its serialized canonical payload and fingerprint SHALL match the pre-change golden vector exactly