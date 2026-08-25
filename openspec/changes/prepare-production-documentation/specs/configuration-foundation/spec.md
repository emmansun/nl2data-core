## MODIFIED Requirements

### Requirement: Versioned strict configuration
The configuration loader SHALL require a supported schema version, reject unknown core fields, and validate required service and runtime configuration before activation. The configuration reference SHALL document core fields, optional dependency profiles, provider configuration, and host-owned endpoint/secret injection without presenting vendor credentials as core configuration.

#### Scenario: Valid configuration activates
- **WHEN** a supported configuration contains required service identity and valid typed values
- **THEN** the loader returns a validated effective configuration snapshot

#### Scenario: Unknown field is rejected
- **WHEN** a configuration contains an unknown field in a strict core section
- **THEN** loading fails before activation with a configuration error identifying the field path

#### Scenario: Documentation matches configuration
- **WHEN** a reader uses a documented optional PostgreSQL, Redis, MongoDB, or OpenAI setup
- **THEN** the documented package and bounded fields match the implementation and host-owned secrets remain outside persisted configuration

### Requirement: Immutable effective snapshot
The loader SHALL compile defaults and supplied configuration into an immutable effective snapshot with a deterministic configuration fingerprint.

#### Scenario: Equivalent inputs have stable identity
- **WHEN** equivalent configurations are loaded in different key orders
- **THEN** they produce equivalent snapshots with the same fingerprint

#### Scenario: Snapshot cannot be changed
- **WHEN** application code attempts to mutate an activated configuration snapshot
- **THEN** the mutation is rejected

### Requirement: Safe secret references
Configuration SHALL support secret references without serializing resolved plaintext values, and production-safe dumping SHALL preserve only references or redacted markers. Documentation examples SHALL use placeholders or ephemeral environment/host secret injection only.

#### Scenario: Secret is not emitted
- **WHEN** a configuration contains a secret reference and is serialized for diagnostics
- **THEN** the output contains no plaintext secret value

### Requirement: Configuration failures fail closed
Invalid schema versions, malformed values, and unsafe protected overrides SHALL prevent activation rather than silently falling back to defaults.

#### Scenario: Unsupported version is rejected
- **WHEN** a configuration declares an unsupported schema version
- **THEN** activation fails with a non-retryable configuration error
