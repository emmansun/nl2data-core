# model-instruction-contract Specification

## Purpose
Define the provider-neutral, bounded model instruction contract and its safe identity across model, workflow, and evaluation boundaries.

## Requirements

### Requirement: Instruction bundles are provider-neutral and versioned
The core SHALL define an immutable, versioned `ModelInstructionBundle` containing bounded system instruction sections, safety constraints, output schema/version, authorized context references, and safe provenance fingerprints. It SHALL be independent of OpenAI, Anthropic, or any vendor message format.

#### Scenario: Bundle is provider-neutral
- **WHEN** a model invocation is prepared for any provider
- **THEN** it can use the same instruction bundle without importing or naming a vendor SDK or message type

#### Scenario: Unsupported instruction version fails closed
- **WHEN** a provider receives an instruction bundle version it cannot support
- **THEN** the invocation is rejected with a normalized configuration/response error before vendor execution

### Requirement: Instruction content is bounded and safe
Instruction bundles SHALL reject credentials, connection strings, raw SQL/MQL, executable code, native objects, raw tenant/principal claims, hidden policy material, and unbounded text or section counts. User prompt content SHALL remain separate from system instructions. Additional model context SHALL satisfy the same bounded JSON and safe-content boundary before provider execution.

#### Scenario: Unsafe instruction material is rejected
- **WHEN** instruction content contains a secret, physical query, executable text, or raw identity claim
- **THEN** bundle validation rejects it before a provider call

#### Scenario: User prompt cannot rewrite system instructions
- **WHEN** a user prompt contains text attempting to replace or disable system constraints
- **THEN** the prompt remains a separate bounded input and the bundle's system instructions remain unchanged

#### Scenario: Unsafe extra context is rejected
- **WHEN** additional model context contains credentials, instruction overrides, physical query text, native objects, or non-JSON values
- **THEN** resolution returns a normalized rejection before provider invocation

### Requirement: Security and semantic provenance are fingerprinted
The bundle SHALL carry stable fingerprints for the instruction version, output schema, authorized Semantic View/model bundle, policy, tenant scope, and context used to assemble it. Raw identity claims, credentials, hidden policy rules, and physical bindings SHALL never appear in the serialized payload or fingerprint inputs.

#### Scenario: Security context change invalidates instructions
- **WHEN** the tenant scope, policy, resolved view, model bundle, output schema, or authorized context changes
- **THEN** the instruction fingerprint changes and old model/workflow evidence is not reused

#### Scenario: Equivalent bundle has stable identity
- **WHEN** equivalent instruction sections are assembled with different mapping insertion orders
- **THEN** canonical serialization and fingerprint remain identical

### Requirement: Providers consume instructions without gaining authority
A provider integration SHALL receive the validated instruction bundle and bounded authorized context, map them to its vendor-specific system/developer channel, and preserve the user prompt separately. Instructions SHALL not grant authorization or bypass IntentResolver, Semantic Query IR validation, governance, or artifact gates.

#### Scenario: Provider mapping preserves the governance boundary
- **WHEN** a provider constructs a vendor request from an instruction bundle
- **THEN** the request contains no credentials or raw physical query and the returned content still passes the existing resolver and governance gates

### Requirement: Instruction identity is included in evidence
Model invocation, workflow checkpoint, and evaluation evidence SHALL include the instruction bundle version/fingerprint and output schema fingerprint when a model call is made, without storing raw instruction text or user prompt content.

#### Scenario: Evidence links model call safely
- **WHEN** a model invocation succeeds or fails
- **THEN** safe evidence can identify the instruction bundle and output contract without exposing raw prompt/instruction payloads
