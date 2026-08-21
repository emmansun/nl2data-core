## ADDED Requirements

### Requirement: Memory records are typed, immutable, and safe
The Memory subsystem SHALL store immutable typed records containing only bounded logical facts, semantic references, protected fingerprints, clarification decisions, or audit references; raw prompts, SQL/MQL, rows/documents, credentials, and native objects SHALL be rejected.

#### Scenario: Raw query content cannot be stored
- **WHEN** a caller attempts to create a memory record containing raw SQL, a prompt transcript, or result rows
- **THEN** record validation rejects the payload before provider storage

#### Scenario: Safe record round-trips deterministically
- **WHEN** an approved logical record is serialized and restored
- **THEN** it remains immutable and produces the same record fingerprint

### Requirement: Memory provider operations are bounded and replaceable
The system SHALL define a provider-neutral Memory protocol for append, recall, compare-and-set, compact, expire, and delete operations with bounded record counts, context size, and retention TTL.

#### Scenario: Recall respects context bounds
- **WHEN** a session contains more records than the configured recall or character budget
- **THEN** recall returns a deterministic bounded projection without returning raw or unrestricted context

#### Scenario: Memory provider failure is explicit
- **WHEN** the configured provider is unavailable
- **THEN** the caller receives a normalized memory-unavailable result and no fabricated context

### Requirement: Memory records are tenant and conversation scoped
Every non-working memory record SHALL bind to a tenant scope fingerprint and bounded session/conversation namespace, with optional adapter and source scope where relevant.

#### Scenario: Cross-tenant recall is denied
- **WHEN** a caller recalls a session under a different tenant scope
- **THEN** no records from the original scope are returned

### Requirement: Retention and deletion are explicit
Memory records SHALL have bounded expiry or retention metadata, and delete/expire operations SHALL remove records without exposing their raw content.

#### Scenario: Expired records are not recalled
- **WHEN** the current time is after a record's expiry
- **THEN** the record is excluded from recall and eligible for bounded cleanup