## 1. Pin MDL Input and Mapping Contract

- [x] 1.1 Confirm the supported Wren MDL JSON schema version(s) from upstream documentation and add representative accepted/rejected source fixtures for models, columns, relationships, measures, and unsupported constructs.
- [x] 1.2 Define and document deterministic entity/field/relationship/measure mappings, required source/bundle/model-version inputs, and the bounded optional identifier-override manifest.
- [x] 1.3 Define stable importer error codes, source-path/line-column behavior, diagnostic bounds, and safe messages that do not echo input values or SQL.

## 2. Implement Bounded Parser and Converter

- [x] 2.1 Add a bounded JSON MDL parser that rejects duplicate keys, invalid encoding, oversized/deep documents, unsupported versions, and unknown semantic structures with typed diagnostics.
- [x] 2.2 Implement deterministic conversion of the supported MDL subset into existing semantic assembly authoring models; reject ambiguous references, identifier collisions, unsupported relationships, and unrepresentable measures/expressions.
- [x] 2.3 Add tests for every supported mapping and stable error-code cases, including unsupported SQL/executable constructs, physical deployment values, missing mappings, and cross-reference failures.

## 3. Validate and Serialize Authoring Output

- [x] 3.1 Serialize candidate output through the existing semantic authoring YAML format and reparse it with `SemanticAssemblyAuthoringLoader`; reject any authoring validation failure before returning output.
- [x] 3.2 Pin byte-identical YAML output for equivalent inputs/options and verify output ordering is independent of JSON member and collection presentation order where semantics are identity-keyed.
- [x] 3.3 Verify failed conversion never returns a successful partial document and diagnostics remain bounded, source-located, and value-safe.

## 4. Add Offline CLI

- [x] 4.1 Add the `nl2data-mdl import` command with required input/output/source/bundle/model-version arguments, optional mapping manifest, and human-readable or JSON diagnostics; register its console entry point.
- [x] 4.2 Write successful output atomically only after full conversion and authoring validation; verify non-zero exit status and no partial output on any failure.
- [x] 4.3 Add CLI integration tests for valid conversion, missing required arguments, unsupported MDL, invalid mapping, overwrite/path handling, deterministic output, and operation without network or database services.

## 5. Documentation and Quality Gates

- [x] 5.1 Document supported Wren MDL versions/constructs, explicit mapping rules, error-code catalog, CLI examples, and lifecycle/security boundaries in the authoring guide.
- [x] 5.2 Run focused parser/converter/CLI tests, the full test suite, lint/type checks, and strict OpenSpec validation; resolve any change-introduced failures.