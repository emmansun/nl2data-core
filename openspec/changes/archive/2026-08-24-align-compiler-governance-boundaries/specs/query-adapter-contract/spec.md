## MODIFIED Requirements

### Requirement: Canonical adapter models
Adapter contracts SHALL use `AsyncMode`, `AdapterCapabilities`, `GeneratedArtifact`, `ParsedArtifact`, `ValidatedArtifact`, and `ExecutionResult`, with `artifact_fingerprint` as the canonical executable identity. MongoDB-specific query models SHALL remain behind its adapter boundary. Compiler-produced artifact evidence SHALL identify the validated canonical Semantic Query IR, current governance/view/model context, and artifact fingerprint without exposing the IR's raw or physical payload through adapter models. Artifact validation SHALL be complete before authorization or execution.

#### Scenario: Artifact lifecycle is representable
- **WHEN** a MongoDB adapter creates, parses, validates, and executes a structured artifact from a validated Semantic Query IR
- **THEN** each stage can be represented by the canonical typed models without backend-specific leakage

#### Scenario: Artifact guard precedes authorization
- **WHEN** an adapter receives an artifact that has not passed its backend-specific guard
- **THEN** authorization is not issued and execution does not start

### Requirement: Fingerprints are safe and stable
Artifact fingerprints SHALL use the `sha256:<lowercase hexadecimal digest>` representation and SHALL exclude raw credentials and unapproved tenant identifiers. Artifact evidence SHALL preserve separate canonical fingerprints for the logical Semantic Query IR, resolved view/model context, policy decision, and physical artifact used to authorize execution.

#### Scenario: Canonical artifact gets a fingerprint
- **WHEN** a validated artifact is canonicalized with the same contract and snapshot inputs
- **THEN** it receives the same fingerprint across repeated calculations

#### Scenario: Logical and physical identities remain distinct
- **WHEN** the same logical IR is compiled for two supported backend targets
- **THEN** each artifact has its own artifact fingerprint while both evidence records reference the same logical IR and current governance context
