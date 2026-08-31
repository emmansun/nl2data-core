## MODIFIED Requirements

### Requirement: Publish and activate are atomic and idempotent
The catalog SHALL publish only verified approved semantic content, compute or verify the semantic fingerprint at the publish boundary, validate that passing Verification Suite evidence is bound to the exact approved draft revision, plan fingerprint, manifest fingerprint, candidate Bundle fingerprint, tenant/source scope, policy profile, runner identity, and executor identities, and atomically persist the immutable artifact, linked accepted-assertion manifest, bounded verification evidence summary/reference, publish audit record, and supersession-chain update. It SHALL atomically activate one complete compatible version per catalog scope. Repeated publish of identical semantic content with equivalent bound passing evidence SHALL be idempotent by fingerprint. Concurrent publication and activation SHALL serialize without exposing partial content or mismatched evidence.

#### Scenario: Concurrent activation leaves one complete active version
- **WHEN** multiple workers activate compatible candidate versions concurrently
- **THEN** each completed operation observes a complete verified published version and the catalog exposes exactly one final active pointer

#### Scenario: Failed activation preserves the active version
- **WHEN** validation, dependency, freshness, drift, scope, database, or required verification-evidence checks fail during activation
- **THEN** no active pointer changes and the failure is safe and retry-classified where appropriate

#### Scenario: Failed publish leaves no partial lifecycle records
- **WHEN** validation, any required verification layer, evidence-binding validation, fingerprinting, manifest/verification/audit persistence, supersession update, or database commit fails
- **THEN** no published bundle, accepted-assertion manifest, verification evidence record, audit record, or supersession edge becomes externally visible

#### Scenario: Published manifest is retrieved by fingerprint
- **WHEN** incremental rediscovery selects a published Bundle fingerprint as its baseline
- **THEN** the catalog returns exactly one immutable accepted-assertion manifest linked to that fingerprint or fails closed when the manifest is missing or mismatched

#### Scenario: Identical publish is idempotent
- **WHEN** a caller retries publish for semantic content whose fingerprint already exists in the scoped catalog with an equivalent plan and passing evidence binding
- **THEN** the catalog returns the existing publication, verification evidence reference, and audit reference without creating duplicate records

## ADDED Requirements

### Requirement: Durable verification evidence is safe and reloadable
The durable catalog SHALL persist a versioned bounded Verification Suite evidence envelope or immutable reference atomically with publication. Reload SHALL validate envelope version, fingerprints, policy profile, runner/executor identities, layer/case statuses, and publication binding before returning it. The envelope SHALL contain no raw rows, scalar values, SQL/MQL, prompts, physical names, deployment references, credentials, native values, or backend exception text.

#### Scenario: Verification evidence survives restart
- **WHEN** a verified Bundle is published and a new catalog worker starts
- **THEN** the worker can retrieve the same bounded suite evidence identity and layer summary linked to the publication fingerprint

#### Scenario: Tampered evidence fails closed
- **WHEN** persisted verification evidence has an unsupported version or mismatched plan, runner, executor, manifest, draft, tenant/source, or Bundle fingerprint
- **THEN** reload and activation reject the record without exposing partial evidence or changing the active pointer
