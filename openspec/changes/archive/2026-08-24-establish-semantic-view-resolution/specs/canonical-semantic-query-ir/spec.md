## MODIFIED Requirements

### Requirement: Canonical Semantic Query IR is versioned and backend-neutral
The planning layer SHALL define an immutable, versioned `SemanticQueryIR` representing logical selections, filters, grouping, ordering, bounded limits, time context, result shape, view/source references, provenance, and capability requirements without embedding SQL, MQL, credentials, executable code, native objects, or presentation configuration. When a Semantic View registry is configured, the IR SHALL bind to a resolved Semantic View identity and fingerprint; unscoped IR is permitted only through explicit compatibility mode.

#### Scenario: Logical request is representable
- **WHEN** a valid semantic request selects bounded fields, applies typed filters, groups or orders results, and declares a result shape
- **THEN** it can be represented as a `SemanticQueryIR` independently of SQL or MongoDB syntax

#### Scenario: Physical payload is rejected
- **WHEN** an IR input contains raw SQL/MQL, a connection string, a driver object, executable code, or chart configuration
- **THEN** strict validation rejects the input before compilation or adapter invocation

#### Scenario: View binding is required when configured
- **WHEN** a Semantic View registry is active and an IR omits or mismatches its resolved-view reference
- **THEN** IR validation rejects it before compilation or adapter invocation

### Requirement: IR serialization and fingerprints are stable
The IR SHALL serialize through canonical JSON with an explicit version and SHALL produce a stable `sha256:<lowercase hexadecimal digest>` fingerprint covering all logical and provenance inputs required for compatibility, including the resolved-view identity and fingerprint when present.

#### Scenario: Equivalent IR has a stable identity
- **WHEN** equivalent IR values are constructed with different mapping insertion orders
- **THEN** their canonical serialized representation and fingerprint are identical

#### Scenario: Version changes affect compatibility
- **WHEN** an IR is loaded with an unsupported version or a different required compatibility version
- **THEN** validation rejects it as incompatible rather than silently interpreting it as the current version

#### Scenario: View change affects IR identity
- **WHEN** the resolved Semantic View fingerprint changes for the same logical selections
- **THEN** the IR fingerprint changes and the old IR is not reused as current

### Requirement: IR validation is bounded and fails closed
The IR validator SHALL enforce identifier, scalar, collection, operator, aggregation, grouping, ordering, limit, extension, provenance, and resolved-view membership constraints before any compiler or adapter is called. Unsupported operations or extensions SHALL produce structured validation issues.

#### Scenario: Unbounded request is rejected
- **WHEN** an IR omits required boundedness or exceeds configured selection, filter, ordering, or limit bounds
- **THEN** validation returns a structured failure and no physical artifact is produced

#### Scenario: Unsupported extension is rejected
- **WHEN** an IR contains an extension node without a matching declared capability
- **THEN** validation fails closed with an unsupported-feature issue

#### Scenario: Member outside view is rejected
- **WHEN** an IR references a field, relationship, operation, or aggregation absent from its resolved Semantic View
- **THEN** validation fails before compilation and reports only a safe member reference
