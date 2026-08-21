## ADDED Requirements

### Requirement: Single canonical QueryAdapter Protocol
The core SHALL define one generic async-first `QueryAdapter` Protocol with async metadata, generation, cost, execution, and close methods plus synchronous side-effect-free parse and validate methods.

#### Scenario: Adapter contract has canonical operations
- **WHEN** a contract test inspects the QueryAdapter Protocol
- **THEN** it finds the DDS-002 operation set and no SQL- or MongoDB-specific method in the core contract

### Requirement: Canonical adapter models
Adapter contracts SHALL use `AsyncMode`, `AdapterCapabilities`, `GeneratedArtifact`, `ParsedArtifact`, `ValidatedArtifact`, and `ExecutionResult`, with `artifact_fingerprint` as the canonical executable identity.

#### Scenario: Artifact lifecycle is representable
- **WHEN** an adapter creates, parses, validates, and executes a generic artifact
- **THEN** each stage can be represented by the canonical typed models without backend-specific leakage

### Requirement: Async capability is explicit
Adapters SHALL declare `async_mode` as `native`, `thread_offload`, or `unsupported`, and the declaration SHALL be inspectable through capabilities.

#### Scenario: Unsupported async mode is visible
- **WHEN** an adapter cannot satisfy the async contract
- **THEN** its capabilities identify `unsupported` rather than relying on an ambiguous boolean combination

### Requirement: Fingerprints are safe and stable
Artifact fingerprints SHALL use the `sha256:<lowercase hexadecimal digest>` representation and SHALL exclude raw credentials and unapproved tenant identifiers.

#### Scenario: Canonical artifact gets a fingerprint
- **WHEN** a validated artifact is canonicalized with the same contract and snapshot inputs
- **THEN** it receives the same fingerprint across repeated calculations
