## Context

NL2Data already has a semantic-only authoring contract, a bounded parser, source-located diagnostics, semantic reference validation, deterministic export, and a lifecycle that keeps imported drafts pending review. There is no MDL importer or MDL fixture in the repository. The importer must therefore be a narrow format adapter, not another assembly model or a shortcut into persistence.

Wren MDL contains constructs that do not necessarily have a one-to-one semantic-assembly representation. A successful import must mean the supported source semantics were mapped and the generated authoring document passed the existing loader; it must not mean every MDL feature was approximated.

## Goals / Non-Goals

**Goals:**
- Provide an offline command that converts a pinned, documented Wren MDL JSON schema subset into the existing semantic assembly authoring YAML format.
- Map models, columns, simple relationship declarations, and only simple measures/expressions that can be represented without loss.
- Produce deterministic, source-located diagnostics with stable mapping error codes and never emit partial YAML for a failed import.
- Require the caller to provide source, bundle, and model-version identities and send generated authoring YAML through the existing loader and validator before output.

**Non-Goals:**
- Executing or embedding MDL `refSql`, SQL expressions, macros, templates, or runtime query logic.
- Inferring physical deployment bindings, source catalog facts, credentials, tenant context, policy authority, review state, approval, or publication.
- Supporting every Wren MDL feature or importing Apache Ossie Metric Language in this change.
- Network access, Wren services, catalog discovery, or draft persistence.

## Decisions

### 1. Pin the source format and support an explicit subset

Implement a version-aware parser for Wren's documented JSON MDL representation. Keep a checked-in representative corpus for the selected upstream schema version(s), including models, columns, relationships, and simple measures. The first implementation task must confirm the exact supported schema version from the upstream format documentation and fixtures before freezing parser behavior. Unknown schema versions and unknown top-level/member shapes fail closed.

Support models and columns as semantic entities and fields; map labels/descriptions where safe. Support simple relationships only when model and key references resolve uniquely and the join form can be represented by the semantic-assembly relationship model. Support only measures that map directly to the authoring `AuthoringMeasure` contract and calculated expressions that match its governed `ExprNode` tree exactly. Ignore nothing silently: an unsupported construct that can affect semantic meaning is an error.

**Alternative considered:** Accept arbitrary MDL JSON and preserve unknown members. Rejected because pass-through payloads could bypass the target schema's safety rules and create false confidence in semantic equivalence.

### 2. Separate mapping from authoring validation

The converter produces an in-memory `SemanticAssemblyAuthoring`-compatible document using only public authoring models. It then serializes to YAML and reparses that YAML with `SemanticAssemblyAuthoringLoader`; only a successful parse and semantic validation can be written. The authoring loader remains the final authority for unique references, safe descriptions, calculated-field rules, policy templates, and schema bounds.

The CLI requires `--source-id`, `--bundle-id`, and `--model-version`. A bounded optional mapping manifest may provide semantic identifiers where deterministic normalization would collide or violate identifier rules. The converter SHALL NOT infer or emit physical table/column bindings; Wren physical references such as `tableReference` and SQL-bearing definitions are either used only to establish a supported semantic identity under documented mapping rules or rejected with a specific mapping error.

**Alternative considered:** Lower directly to `AssemblyDraft`. Rejected because import must not create persistence or lifecycle authority; the existing authoring → validation → trusted host lowering path already enforces the intended boundary.

### 3. Make mapping diagnostics part of the public importer contract

Return structured diagnostics with stable `code`, normalized source path, optional one-based line/column, and controlled message. The initial code family distinguishes invalid input/schema version, unsupported construct, missing required mapping, ambiguous reference, unsupported expression, identifier collision, and generated-authoring validation failure. Messages must not echo arbitrary MDL values or SQL fragments. CLI failure uses a non-zero exit status and machine-readable diagnostics; successful output is deterministic YAML.

**Alternative considered:** Reuse only authoring loader diagnostics. Rejected because loader diagnostics cannot identify why a Wren construct could not be mapped, and callers need stable remediation categories.

### 4. Keep file operations offline and atomic

The command reads the supplied input and optional mapping manifest only, performs no network or external process access, and writes the final output using a temporary file plus atomic replacement after all conversion and validation steps succeed. Input/output paths must be distinct unless an explicit safe overwrite option is used. On any error, no output document is left partially written.

No new runtime dependency is required: use the standard JSON parser with duplicate-key rejection and explicit size bounds, existing authoring/PyYAML facilities for final YAML serialization and validation, and the project's standard-library CLI patterns. Register a console script in `pyproject.toml`.

## Risks / Trade-offs

- **[Risk] Upstream MDL evolves or is underspecified.** → Pin supported schema versions and fixtures; unknown versions/constructs return explicit errors rather than permissive parsing.
- **[Risk] Identifier normalization creates collisions or semantic ambiguity.** → Use deterministic normalization, require explicit overrides in a bounded mapping manifest, and reject unresolved collisions.
- **[Risk] Wren source models carry physical or executable details that do not belong in authoring YAML.** → Never copy them blindly; map only documented semantic members and reject executable/physical-only constructs with stable codes.
- **[Risk] Converter output diverges from the authoring schema.** → Reparse every complete generated document with `SemanticAssemblyAuthoringLoader` and write only after semantic validation succeeds.
- **[Trade-off] Some MDL projects will not import in the first version.** → Document the subset and provide actionable per-construct diagnostics; breadth can be added in later changes with new fixtures and explicit mappings.

## Migration Plan

1. Pin the Wren MDL source version and build representative accepted/rejected fixtures.
2. Implement the bounded parser, mapping manifest, semantic conversion, and stable diagnostics.
3. Validate generated YAML through the existing authoring loader and add deterministic golden-output tests.
4. Add the offline CLI, atomic output behavior, and documentation for supported constructs and error codes.
5. Run importer/parser/CLI tests, full test suite, lint/type checks, and OpenSpec validation. Rollback is removal of the new command/module; no persisted data or schema migration is introduced.

## Open Questions

- Which exact upstream Wren MDL schema version(s) will be pinned for v1? Resolve this from the documented upstream format and checked-in fixtures in task 1.1 before treating the support matrix as final.
- Does the selected Wren MDL version encode relationships in a form that can be parsed into source/target field pairs without interpreting arbitrary SQL? If not, v1 must reject those relationships rather than attempt a partial SQL parser.