## ADDED Requirements

### Requirement: Durable catalog persists audit-evidence envelopes safely
The durable semantic catalog SHALL persist versioned bounded audit-evidence envelopes for lifecycle, publication, activation, and rollback entries. Each envelope SHALL include explicit schema version, artifact kind, tenant/source scope fingerprints, subject references, event identity, event kind, predecessor links, safe outcome/status, entry fingerprint, and bounded payload fields. The catalog SHALL reject unsupported versions, mismatched fingerprints, unsafe payloads, credentials, raw prompts, SQL/MQL, physical names, resolved deployment values, native objects, unrestricted sample values, and raw backend exceptions on write or read.

#### Scenario: Audit evidence survives restart
- **WHEN** lifecycle and publication audit-evidence entries are persisted and a new catalog worker starts
- **THEN** the worker can reload the same bounded entries by scoped subject reference and validate their fingerprints and envelope metadata

#### Scenario: Unsafe audit payload is rejected
- **WHEN** a caller attempts to persist an audit-evidence entry containing raw credentials, resolved deployment values, SQL/MQL, physical names, raw sample rows, native objects, or unbounded text
- **THEN** the catalog rejects the write before persistence and exposes only a normalized safe error

### Requirement: Durable audit evidence validates immutable publication cross-links
For published artifacts, durable audit-evidence reload SHALL validate entries against immutable publication records, accepted-assertion manifest, Verification Suite evidence, publish audit record, frozen release binding, tenant/source scope, and Bundle fingerprint. Reload SHALL NOT require or compare the current mutable assembly draft row for immutable publication history.

#### Scenario: Tampered publication audit evidence fails closed
- **WHEN** persisted audit evidence references a different manifest, verification evidence, publish audit, frozen release binding, tenant/source scope, or Bundle fingerprint than the immutable publication records
- **THEN** reload and activation reject the affected publication evidence without exposing partial records or changing the active pointer

#### Scenario: Draft evolution does not invalidate publication history
- **WHEN** the source draft changes revision, review state, lint status, or verification plan after a publication succeeds
- **THEN** durable audit evidence for the prior publication remains readable and validates against immutable publication records rather than the current draft row

### Requirement: Durable audit history is queryable with bounded retention
The durable catalog SHALL provide scoped bounded lookup of audit-evidence entries by draft, assertion, Bundle fingerprint, publication, activation, rollback, and predecessor reference. Retention and cleanup SHALL preserve entries required by active publications, active pointers, supersession chains, rollback targets, and configured audit retention policy.

#### Scenario: Scoped lookup returns ordered history
- **WHEN** a host lists audit-evidence entries for a scoped Bundle fingerprint or draft reference
- **THEN** the catalog returns a deterministic bounded sequence with cursor metadata when more entries exist

#### Scenario: Cleanup preserves active audit dependencies
- **WHEN** bounded cleanup removes expired inactive audit-evidence entries
- **THEN** entries required to explain active publications, active pointers, supersession chains, rollback targets, and retained publish audit records remain available
