## Purpose

Define the single canonical QueryAdapter protocol shared by all query adapters.

## Requirements

### Requirement: Single canonical QueryAdapter Protocol
The core SHALL define one generic async-first `QueryAdapter` Protocol with async metadata, generation, cost, execution, and close methods plus synchronous side-effect-free parse and validate methods. SQL and MongoDB SHALL implement this contract only as specialization packages.

#### Scenario: Adapter contract has canonical operations
- **WHEN** a contract test inspects the QueryAdapter Protocol
- **THEN** it finds the DDS-002 operation set and no SQL- or MongoDB-specific method in the core contract

#### Scenario: Discovery is optional
- **WHEN** an adapter does not support metadata discovery
- **THEN** it remains a valid QueryAdapter and reports the optional capability as unavailable without affecting query execution

Adapter contracts SHALL use `AsyncMode`, `AdapterCapabilities`, `GeneratedArtifact`, `ParsedArtifact`, `ValidatedArtifact`, and `ExecutionResult`, with `artifact_fingerprint` as the canonical executable identity. MongoDB-specific query models SHALL remain behind its adapter boundary. Compiler-produced artifact evidence SHALL identify the validated canonical Semantic Query IR, current governance/view/model context, and artifact fingerprint without exposing the IR's raw or physical payload through adapter models. Artifact validation SHALL be complete before authorization or execution.

#### Scenario: Artifact lifecycle is representable
- **WHEN** a MongoDB adapter creates, parses, validates, and executes a structured artifact from a validated Semantic Query IR
- **THEN** each stage can be represented by the canonical typed models without backend-specific leakage

#### Scenario: Artifact guard precedes authorization
- **WHEN** an adapter receives an artifact that has not passed its backend-specific guard
- **THEN** authorization is not issued and execution does not start

#### Scenario: Legacy plan compiler is absent
- **WHEN** a compiler is configured for SQL or MongoDB execution
- **THEN** it accepts the canonical IR and does not require `SemanticQueryPlan` or an IR-to-plan adapter

### Requirement: Async capability is explicit
Adapters SHALL declare `async_mode` as `native`, `thread_offload`, or `unsupported`, and the declaration SHALL be inspectable through capabilities.

#### Scenario: Unsupported async mode is visible
- **WHEN** an adapter cannot satisfy the async contract
- **THEN** its capabilities identify `unsupported` rather than relying on an ambiguous boolean combination

### Requirement: Fingerprints are safe and stable
Artifact fingerprints SHALL use the `sha256:<lowercase hexadecimal digest>` representation and SHALL exclude raw credentials and unapproved tenant identifiers. Artifact evidence SHALL preserve separate canonical fingerprints for the logical Semantic Query IR, resolved view/model context, policy decision, and physical artifact used to authorize execution.

#### Scenario: Canonical artifact gets a fingerprint
- **WHEN** a validated artifact is canonicalized with the same contract and snapshot inputs
- **THEN** it receives the same fingerprint across repeated calculations

#### Scenario: Logical and physical identities remain distinct
- **WHEN** the same logical IR is compiled for two supported backend targets
- **THEN** each artifact has its own artifact fingerprint while both evidence records reference the same logical IR and current governance context
