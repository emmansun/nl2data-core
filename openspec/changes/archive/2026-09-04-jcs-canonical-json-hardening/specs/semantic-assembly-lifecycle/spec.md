## ADDED Requirements

### Requirement: Bundle semantic fingerprints use the shared canonical JSON profile
Published Bundle semantic fingerprints SHALL derive from semantic payloads prepared for the shared fingerprint-critical canonical JSON profile. Lifecycle metadata, provenance, review state, review bindings, rejected assertions, deployment bindings, audit records, lint summaries, verification evidence, activation state, and supersession metadata SHALL remain outside the semantic payload and SHALL NOT influence canonical bytes or semantic Bundle fingerprints.

#### Scenario: Lifecycle metadata still does not affect semantic identity
- **WHEN** two publications have identical prepared semantic payloads but different lifecycle metadata, audit evidence, lint readiness references, verification evidence, activation state, or supersession state
- **THEN** their semantic Bundle canonical bytes and fingerprints are identical under the shared canonical JSON profile

#### Scenario: Unsafe semantic payload cannot be fingerprinted
- **WHEN** a semantic assertion payload contains unsupported native values, non-finite numbers, non-string prepared keys, raw credentials, raw SQL/MQL, physical names outside approved model boundaries, or arbitrary objects
- **THEN** semantic payload preparation or canonicalization fails before publication can create a Bundle fingerprint
