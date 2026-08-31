# calculated-field-semantics Delta

## ADDED Requirements

### Requirement: Calculated field expressions are closed, bounded trees
A calculated field SHALL be defined as a frozen expression tree whose leaves are `field` references and scalar constants (`int` only; booleans and floats rejected — the tree lives in the fingerprint domain) and whose operators are exactly `add`, `sub`, `mul`, `div` with two children each. Trees SHALL be bounded in depth and node count. The declared output type SHALL equal the inferred output type: `add`/`sub`/`mul` infer `int` only when both operands infer `int`, otherwise `float`; `div` always infers `float`. The compiler SHALL enforce the declared output type via an explicit CAST in adapter-native output. Operators outside the whitelist, unbounded trees, and declared-output/inferred-output mismatches SHALL fail validation with a structured `CF_001` error naming the offending operator, bound, or both types. Every whitelisted-operator rejection decision (function calls, subqueries, string operations, `CASE WHEN` composition, aggregate composition) SHALL be recorded in an ADR with its alternative path.

#### Scenario: A whitelisted tree validates and enters the fingerprint domain
- **WHEN** a calculated field is defined with an arithmetic tree within bounds over numeric fields of its entity
- **THEN** the definition validates and its content changes the descriptor fingerprint

#### Scenario: A non-whitelisted operator is rejected with CF_001
- **WHEN** an expression tree contains an operator outside the closed whitelist or exceeds a declared bound
- **THEN** validation fails with a structured `CF_001` error naming the operator or bound, and no definition is created

#### Scenario: Output-type coherence is enforced by the inference table
- **WHEN** a calculated field declares an output type that differs from the inferred type (e.g. `int` with a `div` root, or `int` over mixed `int`/`float` operands)
- **THEN** validation fails with a structured `CF_001` variant naming both the declared and inferred types

#### Scenario: Constants are int-only
- **WHEN** an expression contains a float or boolean constant
- **THEN** validation fails; only `int` constants (including negative ints) are expressible

### Requirement: Expression references resolve fail-closed
Every `field` leaf SHALL reference a numeric field of the same entity, and the declared dependency list SHALL exactly equal the set of referenced fields (declaration order is not constrained — set semantics). An unknown reference or a `requires` mismatch SHALL fail with a structured `CF_002` error; a leaf referencing a non-numeric field SHALL fail validation. No expression may reference another calculated field, a different entity, or physical names.

#### Scenario: An unknown field reference fails with CF_002
- **WHEN** an expression references a field that does not exist in the entity, or `requires` does not match the referenced set
- **THEN** validation fails with a structured `CF_002` error and the definition is not usable

#### Scenario: Cross-entity and self-references are impossible
- **WHEN** an expression attempts to reference a field of another entity or another calculated field
- **THEN** validation fails closed; only base fields of the same entity are referencable

#### Scenario: Calculated fields do not compose
- **WHEN** an expression references another calculated field instead of a base field
- **THEN** validation fails with a structured `CF_002` error; composition can only be introduced by a future change that explicitly amends this rule

### Requirement: IR selections reference calculated fields through governed validation
An IR selection MAY reference a declared calculated field by name through the existing selection reference. The name SHALL resolve unambiguously (calculated-field names never collide with entity field ids), and a reference to an identifier that is neither a declared field nor a declared calculated field SHALL fail closed with a structured `CF_003` error. A query whose selections reference calculated fields SHALL declare the `calculated-fields` capability so compilers and adapters that do not support it reject the query through the existing unsupported-capability path.

#### Scenario: An undeclared reference fails closed with CF_003
- **WHEN** an IR selection references an identifier that resolves to no declared field or calculated field
- **THEN** IR validation fails with a structured `CF_003` error and no compilation proceeds

#### Scenario: Referencing a calculated field carries the capability
- **WHEN** a validated selection references a declared calculated field
- **THEN** the IR's required capabilities include `calculated-fields` and unsupported adapters reject it fail-closed

### Requirement: Pii fields never meet calculated-field expressions
The isolation between pii governance and calculated fields is **bidirectional and order-independent**: a calculated field SHALL NOT reference a field declared `pii: true` (rejected at calculated-field definition time), and a `pii` declaration SHALL NOT be applied to a field already referenced by a declared calculated field (rejected at bundle validation time). Both directions SHALL fail with a structured `CF_004` error. The current descriptor has no field-masking policy model; when one is introduced, its field targets SHALL join this same bidirectional check. Derived semantics over masked fields (e.g. mask-then-compute) SHALL be introduced only by a dedicated future ADR, never implicitly.

#### Scenario: A reference to a pii field is rejected at definition time
- **WHEN** a calculated field's expression references a field declared `pii: true`
- **THEN** definition fails with a structured `CF_004` error and no definition is created

#### Scenario: Masking applied after declaration is rejected at bundle validation
- **WHEN** a `pii` declaration is applied to a field already referenced by a declared calculated field
- **THEN** bundle validation fails with a structured `CF_004` error and the bundle is not publishable until the combination is removed

#### Scenario: Masking enforcement stays authoritative
- **WHEN** pii fields exist that no calculated field references
- **THEN** masking behavior is unchanged and no derived output column can bypass it

### Requirement: Compilation expands expressions deterministically at compile time
The compiler SHALL expand a referenced calculated field's expression tree into adapter-native output using the bundle-anchored descriptor snapshot and physical bindings; the expression SHALL never enter the IR and SHALL never be interpreted at runtime. The compiler SHALL re-validate the tree before expansion and fail closed (`CF_001`) if validation does not hold. The selection's aggregation kind SHALL apply uniformly to the expanded expression — including `none`, the legal row-level case. The `zero_division_policy` SHALL govern division semantics: `null` yields NULL/missing, `error` fails execution with the structured `CF_005` error.

#### Scenario: A selection expands to adapter-native output
- **WHEN** a compiled query selects a declared calculated field
- **THEN** the adapter output contains the expanded expression built from physical bindings, with no runtime expression evaluation

#### Scenario: Division semantics follow the declared policy
- **WHEN** an expanded division encounters a zero denominator
- **THEN** the result is NULL/missing under `null` policy and a structured execution failure under `error` policy

### Requirement: Dual adapters produce semantically equivalent results
The same calculated-field definition over the same fixture data SHALL produce semantically equivalent results on the SQL adapter and the MongoDB aggregation pipeline, including identical `zero_division_policy` behavior and equivalent structured failures. The SQL adapter SHALL enforce true division (explicit CAST for integer operands); conformance fixtures SHALL include an `int / int` division case. Conformance tests SHALL pin this equivalence with exact-value assertions.

#### Scenario: The same definition compiles equivalently on both adapters
- **WHEN** a controlled fixture query selects a calculated field on the SQL adapter and the MongoDB adapter
- **THEN** both produce results with identical values for the calculated output on the same data

### Requirement: Evidence records expression hashes only
Compilation evidence SHALL record, for each referenced calculated field, a bounded frozen record of the field name and a fingerprint computed over the canonical expression tree, the `zero_division_policy`, and the `output_type` — never the expression tree itself, physical names, or values. The records SHALL be sorted by field name, so compiling the same IR with different selection orders produces a byte-identical evidence fingerprint. The new evidence member SHALL be omitted from the evidence canonical payload when unset, so evidence for queries without calculated fields keeps its previous fingerprint byte-identically (N6 applied to evidence).

#### Scenario: Referencing evidence carries hashes, not trees
- **WHEN** compilation evidence is produced for a query referencing calculated fields
- **THEN** the evidence contains one name-plus-hash record per referenced field and no expression material

#### Scenario: Evidence without calculated fields is unchanged
- **WHEN** compilation evidence is produced for a query that references no calculated fields
- **THEN** the evidence canonical payload is byte-identical to its pre-introduction shape

#### Scenario: Selection order cannot change the evidence fingerprint
- **WHEN** the same IR referencing the same calculated fields is compiled twice with different selection orders
- **THEN** the evidence hash records are identical (sorted by field name)

### Requirement: Expansion identity is anchored in the evidence chain
Compilation context and evidence SHALL both carry an expansion-implementation identity (`compiler_identity`), compared by the existing symmetric pre-execution guard: identity drift SHALL be rejected, and a one-sided identity (context without evidence or evidence without context) SHALL be rejected. The identity SHALL be introduced on the producer (context) and consumer (evidence) sides in the same release; the one-sided rejection SHALL activate only once both sides populate the identity. The strictness clause (evidence missing the identity is rejected) SHALL activate only when identity versioning is active. Evidence for compilations produced before the identity existed SHALL remain valid while versioning is inactive.

#### Scenario: Expansion-identity drift is rejected
- **WHEN** compilation context and evidence carry different expansion identities
- **THEN** the pre-execution guard rejects the compilation result

#### Scenario: A one-sided expansion identity is rejected
- **WHEN** exactly one of the compilation context and evidence carries an expansion identity
- **THEN** the pre-execution guard rejects the compilation result

#### Scenario: Legacy evidence without identity remains valid while versioning is inactive
- **WHEN** neither context nor evidence carries an expansion identity and identity versioning is inactive
- **THEN** the guard accepts the compilation result unchanged

### Requirement: Calculated fields are discoverable as bounded prompt context
Calculated-field identity (name, label, description, output type) SHALL enter the model instruction bundle as bounded prompt context subject to the existing instruction-bundle bounds and safe-content validation. The model SHALL reference calculated fields by name only; it SHALL never compose, modify, or evaluate expressions, and every reference SHALL be validated.

#### Scenario: The planner references a calculated field by name only
- **WHEN** a model emits a selection referencing a calculated field's name
- **THEN** resolution proceeds through governed validation; any expression material emitted by the model is rejected by existing structural validation
