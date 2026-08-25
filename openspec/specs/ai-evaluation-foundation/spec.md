# ai-evaluation-foundation Specification

## Purpose
TBD - created by archiving change establish-p2-ai-runtime-boundary. Update Purpose after archive.
## Requirements
### Requirement: AI evaluation is deterministic without a live provider
The evaluation system SHALL support fixed model responses for intent, clarification, malformed output, timeout, and injection cases without requiring network access or provider credentials. It SHALL additionally support an opt-in live-provider profile whose result is explicitly classified as verified, unavailable, or skipped.

#### Scenario: Repeated intent evaluation is stable
- **WHEN** the same case, fake response, semantic view, and fixed clock are evaluated twice
- **THEN** the protected intent result and mandatory assertion outcomes are identical

#### Scenario: Live provider is unavailable
- **WHEN** a live evaluation profile lacks credentials or cannot reach the configured service
- **THEN** the evaluation reports `unavailable` or `skipped` and never classifies the case as verified

### Requirement: Unsafe model output is a mandatory failure
AI evaluation SHALL include mandatory assertions that executable-query output, unauthorized semantic references, sensitive context leakage, and unbounded provider retries are rejected.

#### Scenario: Injection attempt cannot pass evaluation
- **WHEN** a fixed provider response attempts to override system constraints or emit executable query text
- **THEN** the case fails before adapter compilation and the report contains no raw provider payload

#### Scenario: Real provider output uses the same gates
- **WHEN** a live OpenAI response contains malformed, unsafe, or out-of-view content
- **THEN** the same resolver assertions reject it before IR compilation or adapter execution

### Requirement: Evaluation evidence is protected
AI evaluation reports SHALL expose only typed intent/clarification outcomes, safe error records, usage bounds, provider/model identity, availability classification, and fingerprints; they SHALL not expose API keys, raw prompts, native provider objects, or unrestricted model responses.

#### Scenario: Malformed provider output is safely reported
- **WHEN** a provider returns a response that fails structured validation
- **THEN** the report records a normalized failure and protected evidence without the malformed raw payload

