# canonical-semantic-query-ir Delta

## ADDED Requirements

### Requirement: Reserved parameterized placeholder schema is capability-gated and fail-closed
The IR SHALL reserve a parameterized placeholder extension kind (`named_query_placeholder`) with a validated payload schema: a bounded query reference (identifier) and a bounded list of typed scalar parameters (`name` identifier; `scalar_type` among `str`, `int`, `float`, `bool`; `required` flag). The payload SHALL be JSON-wire safe and SHALL NOT contain physical names or executable material. The extension SHALL be accepted only when the required capability `named-query-placeholders` is declared; when the capability is absent, construction and every downstream consumer SHALL fail closed through the existing capability gating. In this slice nothing SHALL generate or consume the placeholder: no planner emission, no compiler consumption, no adapter support.

#### Scenario: A placeholder without the capability is rejected
- **WHEN** an IR extension of kind `named_query_placeholder` is present and `named-query-placeholders` is not among the required capabilities
- **THEN** the IR is rejected fail-closed and no compilation or execution proceeds

#### Scenario: An invalid placeholder payload is rejected structurally
- **WHEN** a placeholder payload violates the schema (unknown parameter type, unbounded or non-identifier names, non-JSON material, physical names)
- **THEN** IR construction fails with a structural validation error

### Requirement: The reservation leaves existing IR fingerprints byte-identical
The reservation SHALL NOT change `ir_version` and SHALL NOT alter the canonical payload of any IR that does not carry the extension. A later slice MAY revise or remove the reservation in its own change; removal is fingerprint-safe because unset extensions contribute nothing.

#### Scenario: IRs without the placeholder are unchanged
- **WHEN** the reservation lands and an existing IR carries no placeholder extension
- **THEN** its canonical payload, serialization, and fingerprint are byte-identical to their pre-reservation values
