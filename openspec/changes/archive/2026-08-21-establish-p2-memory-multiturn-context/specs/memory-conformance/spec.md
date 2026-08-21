## ADDED Requirements

### Requirement: Memory conformance is deterministic
The conformance suite SHALL cover safe record creation, cross-tenant isolation, scope mismatch, stale policy/catalog references, expiry, deletion, compaction, unavailable provider fallback, and multi-turn reference resolution using fixed clocks and bounded fixtures.

#### Scenario: Repeated conformance runs agree
- **WHEN** the same records, scope, policy/catalog fingerprints, and fixed clock are evaluated twice
- **THEN** protected evidence and assertion outcomes are identical

### Requirement: Memory security failures are mandatory
Memory conformance SHALL fail when raw prompts/results/secrets enter a record or provider context, when stale references reach execution, or when cross-tenant records are returned.

#### Scenario: Unsafe memory payload fails the suite
- **WHEN** a provider or record contains raw query/result content
- **THEN** the mandatory security assertion fails and the report contains only normalized safe evidence