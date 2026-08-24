## MODIFIED Requirements

### Requirement: Compiler evidence binds artifacts to IR
Every deterministic compiler invocation SHALL receive a validated IR plus immutable compilation context and SHALL emit artifact evidence linked to the IR version and fingerprint, resolved view/model bundle references when configured, compiler identity/version, adapter capability identity, policy fingerprint, and artifact fingerprint. Physical bindings SHALL remain in compiler/model context and SHALL not become part of the canonical logical IR.

#### Scenario: Artifact provenance is reconstructable
- **WHEN** a SQL or MongoDB compiler produces a validated artifact from an IR
- **THEN** the artifact evidence contains the IR, current view/model, policy, compiler, adapter capability, and artifact fingerprints

#### Scenario: Physical binding stays outside IR
- **WHEN** a compiler resolves a semantic field to a backend-specific column or document path
- **THEN** that binding is represented in compilation context/evidence and is absent from the canonical IR payload

#### Scenario: Stale governance context is rejected
- **WHEN** the compilation context has a view, model, policy, tenant, or capability fingerprint that is not current for the IR
- **THEN** no executable artifact is accepted
