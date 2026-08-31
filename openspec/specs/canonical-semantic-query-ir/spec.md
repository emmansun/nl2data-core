## Purpose

Define the immutable, versioned, backend-neutral Semantic Query IR with strict validation, provenance, and view/fingerprint binding.

## Requirements

### Requirement: Canonical Semantic Query IR remains versioned and backend-neutral
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

### Requirement: Legacy plans have no active compatibility contract
The planning layer SHALL use `SemanticQueryIR` as the sole logical query representation. The active system SHALL NOT define or require a `SemanticQueryPlan` model, `plan_to_ir` translator, or `ir_to_plan` translator. New workflow, compiler, AI, and evaluation integrations SHALL consume validated IR directly.

#### Scenario: IR is the only planning input
- **WHEN** a planner, workflow, compiler, or evaluation component accepts a logical query
- **THEN** its contract uses `SemanticQueryIR` and does not construct or translate a legacy plan

#### Scenario: Legacy model is unavailable
- **WHEN** code attempts to import or instantiate `SemanticQueryPlan` from the active planning package
- **THEN** the legacy symbol is absent and no compatibility path is invoked

### Requirement: Compiler evidence binds artifacts to IR
Every deterministic compiler invocation SHALL receive a validated IR plus immutable compilation context and SHALL emit artifact evidence linked to the IR version and fingerprint, resolved view/model bundle references when configured, compiler identity/version, adapter capability identity, policy fingerprint, and artifact fingerprint. A compiler SHALL reject a supplied IR whose identity differs from the IR carried by the context. Physical bindings SHALL remain in compiler/model context and SHALL not become part of the canonical logical IR.

#### Scenario: Artifact provenance is reconstructable
- **WHEN** a SQL or MongoDB compiler produces a validated artifact from an IR
- **THEN** the artifact evidence contains the IR, current view/model, policy, compiler, adapter capability, and artifact fingerprints

#### Scenario: Physical binding stays outside IR
- **WHEN** a compiler resolves a semantic field to a backend-specific column or document path
- **THEN** that binding is represented in compilation context/evidence and is absent from the canonical IR payload

#### Scenario: Stale governance context is rejected
- **WHEN** the compilation context has a view, model, policy, tenant, or capability fingerprint that is not current for the IR
- **THEN** no executable artifact is accepted

### Requirement: Resolved-view references bind IR to an authorized projection
The IR SHALL carry an optional resolved-view reference (view id, version, and fingerprint) and view provenance when produced under a resolved Semantic View, and IR validation SHALL revalidate the reference and every referenced member against the current projection when a resolved view is bound. When no view registry is configured, unbound IR SHALL remain executable without a fabricated view identity (explicit legacy compatibility).

#### Scenario: Bound IR is revalidated against the current view
- **WHEN** an IR carries a view reference whose fingerprint or referenced members no longer match the current resolved projection
- **THEN** validation fails closed with a structured view-binding or member-scope issue before compilation or adapter invocation

#### Scenario: Unbound IR keeps the legacy path
- **WHEN** no view registry is configured and an IR carries no view reference
- **THEN** the IR validates and executes exactly as before without fabricating a view identity

### Requirement: Reserved parameterized placeholder schema is capability-gated and fail-closed
The IR SHALL reserve a parameterized placeholder extension kind (`named_query_placeholder`) with a validated payload schema: a bounded query reference (identifier) and a bounded list of typed scalar parameters (`name` identifier; `scalar_type` among `str`, `int`, `float`, `bool`; `required` flag). The payload SHALL be JSON-wire safe and SHALL NOT contain physical names or executable material. The extension SHALL be accepted only when the required capability `named-query-placeholders` is declared; when the capability is absent, construction and every downstream consumer SHALL fail closed through the existing capability gating. In this slice nothing SHALL generate or consume the placeholder: no planner emission, no compiler consumption, no adapter support.

#### Scenario: A placeholder without the capability is rejected
- **WHEN** an IR extension of kind `named_query_placeholder` is present and `named-query-placeholders` is not among the required capabilities
- **THEN** the IR is rejected fail-closed and no compilation or execution proceeds

#### Scenario: An invalid placeholder payload is rejected structurally
- **WHEN** a placeholder payload violates the schema (unknown parameter type, unbounded or non-identifier names, non-JSON material, physical names)
- **THEN** IR construction fails with a structural validation error

### Requirement: The reservation leaves existing IR fingerprints byte-identical
The reservation SHALL NOT change `ir_version` and SHALL NOT alter the canonical payload of any IR that does not carry the extension. A later slice MAY revise or remove the reservation in its own change; removal is fingerprint-safe because unset extensions contribute nothing.

#### Scenario: IRs without the placeholder are unchanged
- **WHEN** the reservation lands and an existing IR carries no placeholder extension
- **THEN** its canonical payload, serialization, and fingerprint are byte-identical to their pre-reservation values
