## ADDED Requirements

### Requirement: Shared provider preserves the MemoryProvider contract
The shared memory backend SHALL provide an implementation of the existing `MemoryProvider` protocol with the same append, recall, compare-and-set, compact, expire, delete, and availability semantics as the in-memory provider.

#### Scenario: Provider is substitutable
- **WHEN** the public composition layer receives a shared provider wherever a `MemoryProvider` is accepted
- **THEN** existing memory-aware workflow code can use it without importing or branching on a Redis-specific type

### Requirement: Redis remains an optional dependency
The base package SHALL remain importable and the in-memory provider SHALL remain usable when the Redis client package is not installed. Redis-specific imports SHALL be lazy or isolated to the optional provider boundary.

#### Scenario: Base installation has no Redis client
- **WHEN** an application installs the base package without the Redis extra and imports public and in-memory Memory APIs
- **THEN** imports and in-memory operations succeed without attempting to import Redis

#### Scenario: Shared provider without its dependency
- **WHEN** an application attempts to construct the Redis provider without the optional client installed
- **THEN** construction fails with a clear configuration/dependency error that does not affect unrelated providers

### Requirement: Shared records are safe and versioned
The shared backend SHALL persist only validated `MemoryRecord` safe representations in a versioned serialization envelope. It SHALL reject malformed, unknown-version, raw-payload, or scope-invalid values and SHALL never return them as recalled memory.

#### Scenario: Safe record round trip
- **WHEN** a valid bounded `MemoryRecord` is appended and recalled from the shared backend
- **THEN** the returned record preserves its protected fields, payload type, scope, fingerprint, and expiry semantics without raw query data or native objects

#### Scenario: Incompatible stored value
- **WHEN** a stored value has an unsupported schema version or fails `MemoryRecord` validation
- **THEN** the provider excludes it from recall and reports a normalized backend/data error without exposing the stored value

### Requirement: Scope isolation is enforced across replicas
The shared backend SHALL namespace records by configured provider namespace and scope identifiers, and SHALL revalidate tenant, session, conversation, adapter, and source matching before returning records. A missing tenant fingerprint SHALL never recall tenant-bound records.

#### Scenario: Tenant-bound memory is isolated
- **WHEN** one tenant appends a record and another tenant recalls the same session using a different or missing tenant fingerprint
- **THEN** no record from the first tenant is returned

#### Scenario: Replica observes the same scope
- **WHEN** a record is appended through one provider process and recalled through another process configured for the same namespace and Redis service
- **THEN** the second process can recall the record only when its requested scope exactly satisfies the fail-closed matching rules

### Requirement: Mutations are atomic across processes
The shared backend SHALL make record-id uniqueness and compare-and-set fingerprint checks atomic across concurrent provider instances. Compare-and-set SHALL reject a replacement with a different record ID or scope and SHALL return `False` when the expected fingerprint is stale.

#### Scenario: Concurrent append preserves uniqueness
- **WHEN** two provider instances concurrently append different records with the same record ID
- **THEN** exactly one append succeeds and the other receives the normalized duplicate-record error

#### Scenario: Stale compare-and-set cannot overwrite
- **WHEN** two provider instances compare-and-set the same record using the same old expected fingerprint
- **THEN** at most one replacement succeeds and every stale attempt returns `False` without overwriting the winner

### Requirement: Expiry and bounded recall are durable
The shared backend SHALL enforce record TTL, explicit expiry, deletion, compaction, and the existing recall record/character/token budgets across provider instances. Recall and compaction SHALL bound candidate reads and work even when indexes contain stale entries.

#### Scenario: Expired record is not recalled
- **WHEN** a record reaches its expiry time before a recall from another process
- **THEN** the record is omitted and later compaction may remove its storage and index references

#### Scenario: Recall budget remains enforced
- **WHEN** more matching records exist than the requested recall budget permits
- **THEN** the provider returns only the bounded projection and marks it truncated consistently with the in-memory provider

### Requirement: Backend failures degrade through normalized errors
The shared backend SHALL use bounded connection/command timeouts and translate connection, timeout, unavailable, and serialization failures into existing Memory error semantics without exposing credentials or raw backend details. Availability checks SHALL be side-effect bounded and return `False` when the backend cannot respond.

#### Scenario: Redis outage is explicit
- **WHEN** a provider operation cannot reach the configured Redis service
- **THEN** it raises `MEMORY_UNAVAILABLE` with safe bounded details, and the runtime can apply its existing stateless fallback

#### Scenario: Health check does not leak configuration
- **WHEN** `is_available()` encounters a connection failure
- **THEN** it returns `False` and does not include the Redis URL, password, or raw exception text in the result
