## ADDED Requirements

### Requirement: Offline importer maps a bounded Wren MDL subset to semantic authoring
The importer SHALL accept only documented, explicitly supported Wren MDL JSON schema versions and construct a semantic assembly authoring document for supported models/entities, fields, relationships, simple measures, and calculated expressions representable by the existing authoring models. It SHALL require caller-provided source, bundle, and model-version identities. It SHALL NOT execute MDL SQL, call network services, infer physical deployment bindings, or create lifecycle state.

#### Scenario: Supported MDL document converts to authoring YAML
- **WHEN** a supported MDL document and required identity arguments contain uniquely mappable models, fields, relationships, and supported measures
- **THEN** the importer SHALL produce deterministic YAML conforming to the current semantic assembly authoring schema

#### Scenario: Unsupported semantic construct is rejected, not dropped
- **WHEN** an MDL document contains an unknown schema version, unsupported semantic construct, or expression that cannot be represented by the governed authoring models
- **THEN** conversion SHALL fail with a stable mapping error code and SHALL NOT produce a successful partial document

#### Scenario: Import does not create lifecycle authority
- **WHEN** MDL input contains approval, publication, review, audit, credentials, tenant context, or physical deployment values
- **THEN** the importer SHALL reject the unsupported or unsafe content and SHALL NOT create or persist an `AssemblyDraft`

### Requirement: Mapping diagnostics are stable and source-located
Every importer failure SHALL return a bounded diagnostic with a stable error code, normalized source path, optional one-based line/column, and controlled message. The code set SHALL distinguish invalid input or schema version, unsupported construct, missing mapping, ambiguous reference or identifier collision, unsupported expression, and generated-authoring validation failure. Diagnostics SHALL NOT echo raw input values, SQL fragments, credentials, or unbounded parser exceptions.

#### Scenario: Missing or ambiguous mapping has a specific code
- **WHEN** an entity, field, relationship endpoint, or required source identity cannot be mapped uniquely
- **THEN** the diagnostic SHALL identify its source path and use the corresponding stable missing-mapping or ambiguity code

#### Scenario: Unsafe source material is not echoed
- **WHEN** input contains a credential or executable SQL fragment that causes rejection
- **THEN** the diagnostic SHALL identify the rejected path and error category without returning the sensitive or executable value

### Requirement: Generated authoring is revalidated and written atomically
Before output is written, the importer SHALL serialize the complete mapped document and validate it with `SemanticAssemblyAuthoringLoader`. Output SHALL be deterministic for equivalent inputs and options. The CLI SHALL use a non-zero exit status for any parse, mapping, or authoring-validation failure and SHALL leave no partial output file.

#### Scenario: Generated document passes the existing authoring contract
- **WHEN** conversion produces a candidate authoring document
- **THEN** output is written only after the existing loader returns a valid semantic authoring model

#### Scenario: Failed import leaves no partial output
- **WHEN** parsing, mapping, or authoring validation fails during an import
- **THEN** the CLI SHALL exit unsuccessfully and SHALL NOT leave truncated or partially generated YAML at the requested output path

#### Scenario: Equivalent imports are byte-deterministic
- **WHEN** equivalent MDL and mapping inputs are imported with the same identity arguments
- **THEN** the output YAML bytes and ordered diagnostics SHALL be identical

### Requirement: CLI is usable without network or service dependencies
The importer SHALL provide a documented offline command that reads only the specified MDL input and optional bounded mapping manifest, requires source/bundle/model-version identities, and emits YAML or machine-readable diagnostics. The command SHALL not require WrenAI services, database credentials, catalog access, or draft persistence.

#### Scenario: Offline conversion succeeds without external services
- **WHEN** the CLI runs with valid local input and identity arguments while no network or database services are available
- **THEN** conversion and authoring validation SHALL complete without attempting external access

#### Scenario: Required identity arguments are enforced
- **WHEN** the CLI is invoked without required source, bundle, or model-version identity
- **THEN** it SHALL fail with a usage diagnostic before writing output