## ADDED Requirements

### Requirement: Authoring schema accepts a bounded policies section
The authoring schema SHALL accept a top-level bounded `policies` sequence in which each entry declares exactly a policy template name and a bounded parameter mapping of JSON-compatible scalars or bounded scalar lists. The section SHALL NOT accept raw policy payloads, computed fingerprints, lifecycle state, approval bindings, credentials, physical names, or native values. Documents without a `policies` section SHALL parse, validate, lower, and export exactly as before.

#### Scenario: Policy declarations lower into policy assertions
- **WHEN** valid authoring YAML contains a `policies` section with valid template declarations
- **THEN** lowering attaches the equivalent policy assertions to the revision-zero draft alongside the other semantic members

#### Scenario: Unsafe policy content is rejected
- **WHEN** a policy declaration contains a raw policy payload, fingerprint, lifecycle state, credential, or value outside the bounded scalar profile
- **THEN** parsing or model validation fails before draft creation and no unsafe value is echoed

### Requirement: Stable export round-trips policy template declarations
The deterministic authoring export SHALL include policy template declarations, ordered by expanded policy identity independently of document presentation. Export followed by parse and lowering SHALL preserve the expanded policy assertion identities and payload hashes; export SHALL continue to omit lifecycle state, review bindings, fingerprints, and other control-plane metadata.

#### Scenario: Export round-trip preserves policy semantics
- **WHEN** an authoring model with policy declarations is exported and then parsed and lowered again with the same trusted host inputs
- **THEN** the resulting policy assertion identities and payload hashes equal those from the original model

#### Scenario: Export ordering is presentation-invariant
- **WHEN** two equivalent documents differ only in `policies` entry order, key order, comments, or whitespace
- **THEN** their exports are identical
