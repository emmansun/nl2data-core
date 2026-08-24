# structured-intent-resolution Specification

## Purpose
TBD - created by archiving change establish-p2-ai-runtime-boundary. Update Purpose after archive.
## Requirements
### Requirement: Natural language resolves to structured intent only
The intent resolver SHALL convert a public natural-language request and an authorized bounded context into validated structured intent, clarification-required output, or a safe rejection; it SHALL never emit raw executable SQL, MQL, shell text, AST nodes, driver objects, or authorization decisions.

#### Scenario: Intent is handed to semantic planning
- **WHEN** a provider returns a valid intent using authorized semantic references
- **THEN** the resolver emits a typed intent that can be passed to a Semantic Query IR builder

#### Scenario: Ambiguity requires clarification
- **WHEN** the provider cannot select one authorized interpretation with sufficient confidence
- **THEN** the resolver returns clarification-required output containing bounded safe alternatives

### Requirement: Model output is validated before planning
The resolver SHALL reject malformed, out-of-view, oversized, or executable-query-shaped model output before any adapter compilation or execution is attempted.

#### Scenario: Raw SQL output is rejected
- **WHEN** model output contains a SQL text field or equivalent executable query payload
- **THEN** resolution fails safely and the adapter is not invoked

#### Scenario: Unauthorized semantic reference is rejected
- **WHEN** model output references a semantic object outside the authorized semantic view
- **THEN** resolution returns a structured validation or clarification result without broadening the view

### Requirement: Prompt context is minimized and bounded
The resolver SHALL construct provider context from authorized semantic metadata and bounded request information, excluding credentials, native clients, raw result sets, unrestricted schema metadata, and hidden policy state.

#### Scenario: Sensitive context is excluded
- **WHEN** a provider invocation context is assembled for a query
- **THEN** restricted fields, credentials, native objects, and raw prior results are absent from the provider payload

