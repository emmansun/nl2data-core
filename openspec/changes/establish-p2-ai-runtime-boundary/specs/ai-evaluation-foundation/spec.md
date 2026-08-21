## ADDED Requirements

### Requirement: AI evaluation is deterministic without a live provider
The evaluation system SHALL support fixed model responses for intent, clarification, malformed output, timeout, and injection cases without requiring network access or provider credentials.

#### Scenario: Repeated intent evaluation is stable
- **WHEN** the same case, fake response, semantic view, and fixed clock are evaluated twice
- **THEN** the protected intent result and mandatory assertion outcomes are identical

### Requirement: Unsafe model output is a mandatory failure
AI evaluation SHALL include mandatory assertions that executable-query output, unauthorized semantic references, sensitive context leakage, and unbounded provider retries are rejected.

#### Scenario: Injection attempt cannot pass evaluation
- **WHEN** a fixed provider response attempts to override system constraints or emit executable query text
- **THEN** the case fails before adapter compilation and the report contains no raw provider payload

### Requirement: Evaluation evidence is protected
AI evaluation reports SHALL expose only typed intent/clarification outcomes, safe error records, usage bounds, and fingerprints; they SHALL not expose API keys, raw prompts, native provider objects, or unrestricted model responses.

#### Scenario: Malformed provider output is safely reported
- **WHEN** a provider returns a response that fails structured validation
- **THEN** the report records a normalized failure and protected evidence without the malformed raw payload