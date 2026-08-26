## MODIFIED Requirements

### Requirement: Versioned strict configuration
The configuration loader SHALL require a supported schema version, reject unknown core fields, and validate required service and runtime configuration before activation. It SHALL support optional admin API settings for route version, request/job bounds, CORS/network policy references, and host-provided authentication integration without making HTTP or authentication mandatory for core activation.

#### Scenario: Valid configuration activates
- **WHEN** a supported configuration contains required service identity and valid typed values
- **THEN** the loader returns a validated effective configuration snapshot

#### Scenario: Unknown field is rejected
- **WHEN** a configuration contains an unknown field in a strict core section
- **THEN** loading fails before activation with a configuration error identifying the field path

#### Scenario: Admin API configuration is optional
- **WHEN** no admin API profile is configured
- **THEN** the base runtime remains constructible without HTTP framework or authentication dependencies

#### Scenario: Admin bounds are validated
- **WHEN** an admin API profile contains invalid route version, page size, body size, job timeout, polling, or concurrency values
- **THEN** configuration activation fails with a typed configuration error before the server starts

### Requirement: Immutable effective snapshot
The loader SHALL compile defaults and supplied configuration into an immutable effective snapshot with a deterministic configuration fingerprint.

#### Scenario: Equivalent inputs have stable identity
- **WHEN** equivalent configurations are loaded in different key orders
- **THEN** they produce equivalent snapshots with the same fingerprint

#### Scenario: Snapshot cannot be changed
- **WHEN** application code attempts to mutate an activated configuration snapshot
- **THEN** the mutation is rejected

### Requirement: Safe secret references
Configuration SHALL support secret references without serializing resolved plaintext values, and production-safe dumping SHALL preserve only references or redacted markers. Admin API authentication and database credentials SHALL be supplied through host/environment secret injection and SHALL never appear in API responses or logs.

#### Scenario: Secret is not emitted
- **WHEN** a configuration contains a secret reference and is serialized for diagnostics
- **THEN** the output contains no plaintext secret value

### Requirement: Configuration failures fail closed
Invalid schema versions, malformed values, and unsafe protected overrides SHALL prevent activation rather than silently falling back to defaults. Admin API configuration or authentication integration failures SHALL not start an unauthenticated mutation surface.

#### Scenario: Unsupported version is rejected
- **WHEN** a configuration declares an unsupported schema version
- **THEN** activation fails with a non-retryable configuration error
