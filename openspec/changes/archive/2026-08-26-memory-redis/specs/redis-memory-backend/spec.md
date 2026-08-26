## ADDED Requirements

### Requirement: Redis backend implements the MemoryProvider contract
The package SHALL implement the core `MemoryProvider` protocol with equivalent append, recall, compare-and-set, compact, expire, delete, and availability semantics.

#### Scenario: Provider is substitutable
- **WHEN** a host injects the Redis provider wherever a `MemoryProvider` is accepted
- **THEN** memory-aware workflow code operates without branching on Redis-specific types

#### Scenario: Safe fallback remains available
- **WHEN** Redis is unavailable during a memory operation
- **THEN** the runtime receives the normalized memory-unavailable result and can continue with stateless behavior

### Requirement: Redis records are safe, versioned, and bounded
The backend SHALL persist only validated `MemoryRecord` safe representations in an explicit versioned envelope, enforce configured record/namespace/recall/retention bounds, and reject malformed, unknown-version, raw-payload, or scope-invalid values.

#### Scenario: Safe record round-trips
- **WHEN** a valid bounded record is appended and recalled
- **THEN** the record preserves its protected fields, payload type, scope, fingerprint, and expiry without raw query data or native objects

#### Scenario: Invalid stored value is excluded
- **WHEN** a stored Redis value fails envelope or `MemoryRecord` validation
- **THEN** the provider does not return it as memory and reports a normalized bounded data error

### Requirement: Scope isolation is enforced across replicas
The backend SHALL namespace records by configured provider namespace, tenant scope fingerprint/global marker, session/conversation scope, and record identity, then revalidate exact scope before returning records. Raw tenant or principal identifiers SHALL never become key components.

#### Scenario: Tenant-bound records are isolated
- **WHEN** one tenant appends a record and another tenant recalls the same session using a different or missing tenant fingerprint
- **THEN** no record from the first tenant is returned

#### Scenario: Separate processes share one scope
- **WHEN** one provider process appends a record and another process uses the same Redis service and namespace
- **THEN** the second process can recall it only when the requested scope exactly matches

### Requirement: Redis mutations are atomic across processes
The backend SHALL enforce record-id uniqueness and compare-and-set fingerprint checks atomically across provider instances. Compare-and-set SHALL reject a replacement with a different record ID or scope and SHALL return false for a stale expected fingerprint.

#### Scenario: Concurrent append preserves uniqueness
- **WHEN** two providers append different records with the same record ID concurrently
- **THEN** exactly one succeeds and the other receives a normalized duplicate-record error

#### Scenario: Stale compare-and-set cannot overwrite
- **WHEN** two providers compare-and-set the same record using the same old fingerprint
- **THEN** at most one replacement succeeds and stale attempts do not overwrite the winner

### Requirement: TTL and bounded recall are durable
The backend SHALL enforce record expiry, explicit expire/delete, bounded compaction, and existing recall record/character/token budgets across provider instances. Stale index entries SHALL be tolerated without unbounded scans.

#### Scenario: Expired record is omitted
- **WHEN** a record reaches its expiry before recall from another process
- **THEN** it is omitted and eligible for bounded cleanup

#### Scenario: Recall budget remains bounded
- **WHEN** more matching records exist than the configured recall budget permits
- **THEN** the provider returns only the bounded projection and reports truncation consistently

### Requirement: Redis dependency and failures are optional and normalized
The package SHALL load redis-py lazily, support host-injected clients or URLs, use bounded connection/command timeouts, normalize Redis failures through existing Memory error semantics, and keep Redis keys separate from other backend records.

#### Scenario: Base import remains Redis-free
- **WHEN** an application imports the base package without the Redis extra
- **THEN** public and in-memory Memory APIs remain usable and redis-py is not required or loaded

#### Scenario: Redis outage is safe
- **WHEN** a provider operation cannot reach Redis or times out
- **THEN** it returns a normalized retryable memory-unavailable error without exposing the URL, password, or raw driver exception
