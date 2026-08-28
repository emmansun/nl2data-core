# evaluation-runner Delta

## ADDED Requirements

### Requirement: Evaluation reports carry value-semantics attribution
For corpus questions whose filters target enum-coded fields, the evaluation report SHALL record per-question value-semantics attribution distinguishing: a filter value resolved from the declared mapping (`VS_HIT`), a declared mapping that the value missed (`VS_MISS`), and a field with no value semantics declared (`VS_UNPOLICIED`). Attribution SHALL be bounded, evidence-safe, and reported per run so stage gates can read the hit rate.

#### Scenario: Annotated hit question reports VS_HIT
- **WHEN** a corpus question annotated with an expected mapping hit resolves its filter value from the declared mapping
- **THEN** the report records `VS_HIT` attribution for that question

#### Scenario: A miss is attributed, not silently degraded
- **WHEN** a corpus question's filter value fails to resolve against a declared mapping
- **THEN** the report records `VS_MISS` attribution and the case outcome reflects the resolution failure

#### Scenario: Unpolicied fields are measured separately
- **WHEN** a corpus question filters an enum-coded field with no value semantics declared
- **THEN** the report records `VS_UNPOLICIED` attribution, distinct from `VS_MISS`

#### Scenario: Attribution is evidence-safe
- **WHEN** the evaluation report is serialized
- **THEN** attribution output contains bounded codes and counts, never raw result rows or physical values

#### Scenario: Attribution granularity is per value, per case, per run
- **WHEN** a case contains enum-filter occurrences whose individual values resolve differently, including mixed `in` lists
- **THEN** each value outcome is recorded individually, aggregated per case, and summarized per run
