## ADDED Requirements

### Requirement: Fingerprint-critical payloads use a shared JCS-compatible canonical JSON contract
The system SHALL define one shared canonical JSON contract for fingerprint-critical payloads. The contract SHALL emit UTF-8 JSON without a byte order mark, sort object member names deterministically, use minimal JSON whitespace, preserve array order, preserve string values prepared by domain models, reject duplicate prepared object names, reject unsupported native values, and produce `sha256:<lowercase hexadecimal digest>` fingerprints from the canonical UTF-8 bytes.

#### Scenario: Equivalent JSON-safe payloads have one identity
- **WHEN** two JSON-safe payloads contain the same object members and values with different mapping insertion orders
- **THEN** the canonical bytes and `sha256:` fingerprint are identical

#### Scenario: Array order remains semantic
- **WHEN** two payloads differ only in the order of an array value
- **THEN** the canonical bytes and fingerprint differ unless the owning domain model explicitly sorted that array before canonicalization

### Requirement: Canonicalization rejects unsafe or non-JSON values
The canonicalization boundary SHALL accept only JSON object, array, string, integer, finite number, boolean, and null values prepared by domain models. It SHALL reject datetimes, sets, tuples, bytes, decimals without an explicit domain representation, callables, native clients, exceptions, enums, arbitrary objects, NaN, Infinity, negative Infinity, and values whose object keys are not prepared strings.

#### Scenario: Native object is rejected rather than stringified
- **WHEN** a fingerprint-critical payload contains a datetime, set, bytes value, exception, callable, enum, or arbitrary object
- **THEN** canonicalization fails closed with a safe structured error and does not call `str()` to create a fingerprint

#### Scenario: Non-finite number is rejected
- **WHEN** a fingerprint-critical payload contains NaN, Infinity, or negative Infinity
- **THEN** canonicalization rejects the payload before bytes or fingerprints are produced

### Requirement: Canonicalization profile and golden vectors are versioned
The system SHALL define a canonicalization profile identifier for the strict JCS-compatible algorithm and SHALL publish golden vectors for representative payloads and edge cases. Persisted artifacts or evidence that require long-term reload validation SHALL record the canonicalization profile used for their fingerprint-critical envelope or identity.

#### Scenario: Persisted envelope records canonicalization profile
- **WHEN** a fingerprinted catalog envelope, workflow checkpoint, verification evidence record, or audit evidence record is persisted under the strict profile
- **THEN** the safe envelope records the canonicalization profile needed to recompute and validate its identity later

#### Scenario: Golden vector catches serializer drift
- **WHEN** the canonical encoder changes object ordering, string escaping, number rendering, whitespace, UTF-8 bytes, or unsafe-value rejection behavior
- **THEN** golden-vector tests fail before persisted identities can drift silently

### Requirement: Legacy canonicalization remains explicit and bounded
Historical records whose fingerprints were produced by an older canonicalization profile SHALL remain explainable through an explicit legacy profile or additive migration path. The system SHALL NOT silently reinterpret legacy fingerprints as strict JCS-compatible identities when the original canonical bytes cannot be reproduced under the strict profile.

#### Scenario: Legacy record is classified explicitly
- **WHEN** a persisted artifact has a fingerprint created under the previous deterministic JSON profile
- **THEN** reload classifies it with the legacy profile or an explicit migration result rather than treating it as a strict JCS profile record

#### Scenario: Unknown canonicalization profile fails closed
- **WHEN** persisted evidence or an envelope declares an unsupported canonicalization profile
- **THEN** reload rejects it with a safe incompatible-profile error and does not expose it as validated current evidence
