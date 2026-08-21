## ADDED Requirements

### Requirement: Vendor-neutral telemetry ports
The telemetry foundation SHALL define typed ports for structured logs, spans, metrics, audit events, and request correlation without requiring a commercial or vendor-specific backend.

#### Scenario: In-memory telemetry is usable
- **WHEN** a component emits a valid log, span, metric, or audit event through the P0 port
- **THEN** an in-memory test sink can receive and inspect the typed event

### Requirement: Correlation uses opaque identifiers
Telemetry context SHALL carry opaque request and workflow identifiers plus optional configuration, policy, metadata, semantic, and artifact fingerprints without embedding raw identity or authorization claims.

#### Scenario: Context does not authorize
- **WHEN** telemetry context is propagated to a component
- **THEN** it can correlate records but cannot be used as an authorization decision or identity source

### Requirement: Sensitive data is redacted by default
Telemetry serialization SHALL reject or omit credentials, raw results, raw queries, and unrestricted prompt content under the default safe profile.

#### Scenario: Unsafe field is suppressed
- **WHEN** an event includes a forbidden raw payload
- **THEN** the emitted safe event contains no forbidden payload

### Requirement: Telemetry degradation is bounded
Telemetry sink failure SHALL be observable and SHALL NOT silently disable mandatory application errors or authorization boundaries.

#### Scenario: Sink failure does not grant access
- **WHEN** a telemetry sink is unavailable during a request
- **THEN** the request does not bypass its required control path and the degradation is represented safely
