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

### Requirement: Enum filter values resolve deterministically before planning
The intent resolver SHALL resolve filter values that are business words against the declared field value mapping during intent resolution, before the canonical IR is built and frozen. Resolution SHALL use deterministic lookup only. A miss under the reject policy SHALL produce a structured resolution failure carrying a stable error code, the field, the attempted value, and the known business terms; no IR SHALL be produced. A miss under the warn policy SHALL proceed with a recorded warning. Fields without declared value semantics SHALL resolve exactly as before.

#### Scenario: Filter value resolves from the governed mapping
- **WHEN** an intent filter uses a business word present in the field's value mapping
- **THEN** the resolver emits a typed intent whose IR filter carries the mapped stored value

#### Scenario: Rejected miss offers clarification material
- **WHEN** an intent filter uses a business word absent from the mapping under the reject policy
- **THEN** the resolver returns a structured failure (not a guessed literal) whose known-business-terms context can drive a clarification request to the requester

#### Scenario: Warn-policy miss is recorded and proceeds
- **WHEN** the field declares the warn policy and the filter value misses the mapping
- **THEN** resolution succeeds with a recorded warning and the emitted IR is otherwise identical to the mapping-hit form

#### Scenario: Unauthorized or malformed value material is still rejected
- **WHEN** a filter value contains executable or non-scalar material regardless of value semantics
- **THEN** existing validation rejects it without invoking value resolution

### Requirement: Resolution reads the bundle-anchored snapshot and accepts governed stored values
The resolver SHALL perform mapping lookups against the descriptor snapshot referenced by the active bundle (by catalog fingerprint) and SHALL fail closed when that snapshot is unavailable. The resolver SHALL accept filter values that equal a stored value of the declared mapping, recording them as pass-through. In v4.1, filters on fields with declared value semantics SHALL be limited to the `eq` and `in` operators.

#### Scenario: Stale-registry lookup is impossible
- **WHEN** the live registry differs from the bundle-anchored snapshot
- **THEN** resolution uses the snapshot so the frozen IR stays consistent with the evidence's bundle fingerprint

#### Scenario: Snapshot unavailable fails closed
- **WHEN** the descriptor snapshot referenced by the active bundle cannot be located
- **THEN** resolution fails closed and no IR is produced

#### Scenario: Direct stored-value output passes through
- **WHEN** the model emits a stored value present in the declared mapping
- **THEN** the intent resolves without clarification and the outcome is recorded as pass-through

#### Scenario: Comparison-unsafe operator on a mapped field is rejected
- **WHEN** a filter applies an operator other than `eq` or `in` to a field with declared value semantics
- **THEN** resolution returns a structured `VS_002` operator rejection (field, attempted operator, allowed operators) without producing an IR

