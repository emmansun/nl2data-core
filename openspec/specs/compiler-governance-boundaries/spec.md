# compiler-governance-boundaries Specification

## Purpose
Define the shared compiler, artifact guard, authorization, and result-lineage boundary for governed query execution.

## Requirements

### Requirement: Compiler context is explicit and immutable
The system SHALL define a backend-neutral immutable compilation context containing the validated Semantic Query IR, resolved Semantic View and active model/bundle fingerprints when configured, trusted tenant/purpose references, adapter capabilities, policy fingerprint, and effective bounds. Physical bindings MAY be supplied as compiler-specific context and SHALL not enter the logical IR. A compiler SHALL reject a supplied IR whose identity differs from the IR carried by the context.

#### Scenario: Compiler receives governed context
- **WHEN** a SQL or MongoDB compiler is invoked for a query
- **THEN** it receives validated IR and current context evidence rather than untrusted raw request claims

#### Scenario: Missing required context fails closed
- **WHEN** compilation requires a bound view, tenant scope, policy, or physical binding and that context is absent
- **THEN** compilation fails before an executable artifact is accepted

### Requirement: Logical and physical evidence remain linked but distinct
Compiler and guard evidence SHALL link IR, view/model bundle, policy, adapter capability, physical artifact, authorization, and protected result fingerprints without persisting raw SQL/MQL, credentials, tenant identity, native objects, or result values.

#### Scenario: Cross-backend evidence is reconstructable
- **WHEN** the same IR is compiled for SQL and MongoDB
- **THEN** each artifact has a distinct artifact identity while evidence links both to the same logical and current governance references

#### Scenario: Sensitive material is excluded
- **WHEN** compilation or guard evidence is serialized
- **THEN** raw physical artifacts and credentials are absent and only bounded safe references remain

### Requirement: Compilers cannot grant authority
A compiler SHALL only translate validated IR into a backend artifact and facts. It SHALL NOT authorize execution, broaden policy scope, bypass view membership, or suppress mandatory obligations. Artifact-specific guards SHALL reject unsafe or unsupported physical artifacts before execution.

#### Scenario: Compiler cannot bypass policy
- **WHEN** a compiler emits an artifact that omits a required policy filter or references an unauthorized field
- **THEN** the guard or governance boundary rejects it before authorization or adapter execution

### Requirement: Guard and authorization ordering is mandatory
The runtime SHALL enforce the order `IR/view validation -> compilation -> artifact parse/guard -> governance decision -> execution authorization -> bounded execution -> result protection`. No adapter execution SHALL start before every preceding gate succeeds. Immediately before execution, the boundary SHALL reverify IR, view/model, policy, tenant, purpose, capability, artifact, obligation, authorization, and effective-limit evidence.

#### Scenario: Pre-execution gate failure stops the adapter
- **WHEN** IR validation, artifact guarding, governance, or authorization fails
- **THEN** the adapter is not invoked and a safe structured outcome is returned

#### Scenario: Authorization is rechecked
- **WHEN** the artifact, tenant context, policy, capability, or effective limits differ before execution
- **THEN** authorization verification rejects the operation before database access

### Requirement: Protected results retain decision lineage
Result protection SHALL operate on the normalized execution result before it crosses the public or Memory boundary, and final safe evidence SHALL link protected result identity to IR, view/model, policy, artifact, adapter, and authorization references.

#### Scenario: Result lineage is complete
- **WHEN** a governed query succeeds
- **THEN** its protected result evidence can identify the logical IR and policy/artifact decisions without exposing raw result values

### Requirement: Pre-execution guard verifies planner identity consistency
The pre-execution guard SHALL reject execution when compilation evidence carries a planner identity that differs from the planner identity in the compilation context, and when exactly one of the two sides carries a planner identity while the other lacks one. Once planner-identity versioning is actively used, evidence missing a planner identity SHALL be rejected outright.

#### Scenario: Planner identity drift is rejected before execution
- **WHEN** compilation evidence was produced under a planner identity different from the current context's planner identity
- **THEN** the guard returns a human-safe planner-identity drift reason and execution is not attempted

#### Scenario: Evidence without the context's planner identity is rejected
- **WHEN** the compilation context carries a planner identity but the evidence lacks one
- **THEN** the guard fails with a planner-identity reason before adapter execution

#### Scenario: Evidence identity without the context's planner identity is rejected
- **WHEN** compilation evidence carries a planner identity but the context lacks one
- **THEN** the guard fails with a planner-identity reason because one-sided identity cannot be drift-checked

#### Scenario: Both sides unset keeps legacy direct-compile paths working
- **WHEN** neither the context nor the evidence carries a planner identity and identity versioning is not active
- **THEN** the guard makes no planner-identity determination and existing behavior is unchanged
