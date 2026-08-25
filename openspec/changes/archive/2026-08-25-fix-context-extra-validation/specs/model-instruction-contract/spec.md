## MODIFIED Requirements

### Requirement: Instruction content is bounded and safe

Instruction bundles SHALL reject credentials, connection strings, raw SQL/MQL, executable code, native objects, raw tenant/principal claims, hidden policy material, and unbounded text or section counts. User prompt content SHALL remain separate from system instructions. Additional model context SHALL satisfy the same bounded JSON and safe-content boundary before provider execution. Authorized bounded configuration fields (such as output-token limits) SHALL be accepted as extra context and SHALL NOT be rejected solely because the field name contains a credential marker substring.

#### Scenario: Unsafe extra context is rejected

- **WHEN** additional model context contains credentials, instruction overrides, physical query text, native objects, or non-JSON values
- **THEN** resolution returns a normalized rejection before provider invocation

#### Scenario: Authorized bounded configuration passes the extra-context guard

- **WHEN** additional model context carries a bounded configuration field such as `max_output_tokens` alongside recalled memory references
- **THEN** the context is accepted and provider invocation proceeds without rejecting the field by name
