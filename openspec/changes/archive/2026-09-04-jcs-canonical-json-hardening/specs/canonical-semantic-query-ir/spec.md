## ADDED Requirements

### Requirement: IR fingerprints use the shared canonical JSON profile
Semantic Query IR canonical serialization and fingerprints SHALL use the shared fingerprint-critical canonical JSON profile. IR models SHALL prepare all values into JSON-safe payloads before canonicalization, SHALL reject unsupported native values rather than stringify them, and SHALL keep the existing `sha256:<lowercase hexadecimal digest>` fingerprint representation.

#### Scenario: IR rejects native canonicalization inputs
- **WHEN** an IR payload or extension attempts to include a datetime, set, bytes value, enum, callable, native object, NaN, Infinity, or non-string prepared key
- **THEN** IR construction or serialization fails closed before canonical bytes or fingerprints are produced

#### Scenario: Existing safe IR vectors are pinned
- **WHEN** a supported IR fixture is serialized under the shared canonical JSON profile
- **THEN** its canonical bytes, profile metadata where applicable, and fingerprint match checked-in golden vectors
