## MODIFIED Requirements

### Requirement: Legacy plans have no active compatibility contract
The planning layer SHALL use `SemanticQueryIR` as the sole logical query representation. The active system SHALL NOT define or require a `SemanticQueryPlan` model, `plan_to_ir` translator, or `ir_to_plan` translator. New workflow, compiler, AI, and evaluation integrations SHALL consume validated IR directly.

#### Scenario: IR is the only planning input
- **WHEN** a planner, workflow, compiler, or evaluation component accepts a logical query
- **THEN** its contract uses `SemanticQueryIR` and does not construct or translate a legacy plan

#### Scenario: Legacy model is unavailable
- **WHEN** code attempts to import or instantiate `SemanticQueryPlan` from the active planning package
- **THEN** the legacy symbol is absent and no compatibility path is invoked

### Requirement: Canonical Semantic Query IR remains versioned and backend-neutral
The planning layer SHALL define an immutable, versioned `SemanticQueryIR` representing logical selections, filters, grouping, ordering, bounded limits, time context, result shape, view/source references, provenance, and capability requirements without embedding SQL, MQL, credentials, executable code, native objects, or presentation configuration.

#### Scenario: Logical request is representable
- **WHEN** a valid semantic request selects bounded fields, applies typed filters, groups or orders results, and declares a result shape
- **THEN** it can be represented as a `SemanticQueryIR` independently of SQL or MongoDB syntax

#### Scenario: Physical payload is rejected
- **WHEN** an IR input contains raw SQL/MQL, a connection string, a driver object, executable code, or chart configuration
- **THEN** strict validation rejects the input before compilation or adapter invocation

### Requirement: IR serialization and fingerprints are stable
The IR SHALL serialize through canonical JSON with an explicit version and SHALL produce a stable `sha256:<lowercase hexadecimal digest>` fingerprint covering all logical and provenance inputs required for compatibility.

#### Scenario: Equivalent IR has a stable identity
- **WHEN** equivalent IR values are constructed with different mapping insertion orders
- **THEN** their canonical serialized representation and fingerprint are identical

#### Scenario: Version changes affect compatibility
- **WHEN** an IR is loaded with an unsupported version or a different required compatibility version
- **THEN** validation rejects it as incompatible rather than silently interpreting it as the current version

### Requirement: IR validation is bounded and fails closed
The IR validator SHALL enforce identifier, scalar, collection, operator, aggregation, grouping, ordering, limit, extension, and provenance constraints before any compiler or adapter is called. Unsupported operations or extensions SHALL produce structured validation issues.

#### Scenario: Unbounded request is rejected
- **WHEN** an IR omits required boundedness or exceeds configured selection, filter, ordering, or limit bounds
- **THEN** validation returns a structured failure and no physical artifact is produced

#### Scenario: Unsupported extension is rejected
- **WHEN** an IR contains an extension node without a matching declared capability
- **THEN** validation fails closed with an unsupported-feature issue

### Requirement: Compiler evidence binds artifacts to IR
Every deterministic compiler invocation SHALL receive a validated IR plus compilation context and SHALL emit artifact evidence linked to the IR version and fingerprint. Physical bindings SHALL remain in compiler/model context and SHALL not become part of the canonical logical IR.

#### Scenario: Artifact provenance is reconstructable
- **WHEN** a SQL or MongoDB compiler produces a validated artifact from an IR
- **THEN** the artifact evidence contains the IR fingerprint, compiler identity/version, adapter capability identity, and artifact fingerprint

#### Scenario: Physical binding stays outside IR
- **WHEN** a compiler resolves a semantic field to a backend-specific column or document path
- **THEN** that binding is represented in compilation context/evidence and is absent from the canonical IR payload
