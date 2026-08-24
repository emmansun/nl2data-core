## MODIFIED Requirements

### Requirement: Canonical adapter models
Adapter contracts SHALL use `AsyncMode`, `AdapterCapabilities`, `GeneratedArtifact`, `ParsedArtifact`, `ValidatedArtifact`, and `ExecutionResult`, with `artifact_fingerprint` as the canonical executable identity. MongoDB-specific query models SHALL remain behind its adapter boundary. Compiler-produced artifact evidence SHALL additionally identify the validated canonical Semantic Query IR version and fingerprint without exposing the IR's raw or physical payload through adapter models.

#### Scenario: Artifact lifecycle is representable
- **WHEN** a MongoDB adapter creates, parses, validates, and executes a structured artifact
- **THEN** each stage can be represented by the canonical typed models without backend-specific leakage

#### Scenario: Artifact is linked to canonical IR
- **WHEN** a compiler produces a validated artifact from a Semantic Query IR
- **THEN** its safe evidence links the artifact fingerprint to the IR version and fingerprint

### Requirement: Fingerprints are safe and stable
Artifact fingerprints SHALL use the `sha256:<lowercase hexadecimal digest>` representation and SHALL exclude raw credentials and unapproved tenant identifiers. Artifact evidence SHALL preserve the separate canonical fingerprint of the logical Semantic Query IR used to produce the artifact.

#### Scenario: Canonical artifact gets a fingerprint
- **WHEN** a validated artifact is canonicalized with the same contract and snapshot inputs
- **THEN** it receives the same fingerprint across repeated calculations

#### Scenario: Logical and physical identities remain distinct
- **WHEN** the same logical IR is compiled for two supported backend targets
- **THEN** each artifact has its own artifact fingerprint while both evidence records reference the same logical IR fingerprint
