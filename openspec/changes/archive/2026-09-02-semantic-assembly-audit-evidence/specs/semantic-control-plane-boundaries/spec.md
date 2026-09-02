## ADDED Requirements

### Requirement: Audit evidence crosses boundaries through immutable contracts
Control-plane modules SHALL exchange assembly audit evidence only through bounded immutable contracts owned by the core control-plane boundary. Domain model modules SHALL NOT import Admin DTOs, PostgreSQL repositories, telemetry sinks, or transport objects to construct or inspect audit evidence. Catalog ports SHALL receive audit-evidence entries and publication audit bindings as part of immutable lifecycle/publication contracts rather than mutable drafts or untyped dictionaries.

#### Scenario: Optional packages consume audit ports inward only
- **WHEN** Admin or PostgreSQL catalog code creates, persists, or reads audit-evidence entries
- **THEN** it imports core audit-evidence contracts and ports while core domain modules do not import optional package implementations

#### Scenario: Untyped audit dictionaries are rejected by architecture tests
- **WHEN** a control-plane change passes audit evidence across module boundaries as raw dictionaries, `Any`, or optional parallel argument groups instead of typed contracts
- **THEN** architecture or type-checking tests fail with the violated boundary

### Requirement: Audit evidence remains distinct from semantic identity and telemetry sinks
Audit-evidence entry fingerprints SHALL identify bounded audit facts and cross-links, but SHALL NOT enter semantic Bundle canonical payloads or change semantic Bundle fingerprints. Telemetry/audit sinks MAY receive safe references derived from audit-evidence entries, but sink delivery SHALL NOT be required for lifecycle persistence, publication idempotency, activation, rollback, or Admin inspection.

#### Scenario: Audit sink outage does not corrupt persisted evidence
- **WHEN** a host telemetry or audit sink is unavailable while lifecycle persistence succeeds
- **THEN** persisted audit-evidence contracts, publication records, activation state, and Admin inspection remain valid and do not depend on sink delivery

#### Scenario: Audit evidence does not perturb Bundle identity
- **WHEN** two publications contain identical semantic payloads but have different operator audit references, lint readiness references, or lifecycle audit-evidence entry IDs
- **THEN** their semantic Bundle fingerprint remains determined only by semantic payload while audit-evidence fingerprints remain separate