# Semantic Assembly YAML Authoring

> **Reader**: semantic model owners and control-plane integrators.
> **Prerequisites**: [Metadata to Bundle](metadata-to-bundle.md).
>
> **Language**: English is normative. See the
> [Chinese translation](semantic-assembly-authoring.zh-CN.md).

Semantic authoring YAML is a human-oriented, semantic-only input format. It is
not the internal `AssemblyDraft` envelope and cannot carry review, approval,
publication, activation, audit, provenance, revision, assertion identity, or
resolved connection data.

## Document shape

Every document declares:

- `apiVersion: nl2data.io/semantic-assembly-authoring/v1alpha1`
- `kind: SemanticAssembly`
- `metadata.bundleId` and `metadata.modelVersion`
- `spec.source.sourceId` and at least one entity

`spec` may also contain fields, nested relationships and calculated fields,
measures, grains, source references, compatibility, and deployment bindings.
See the [complete demo document](../../demo/authoring/sales-semantic-assembly.yaml).

Identifiers are 1-128 characters and use letters, digits, `_`, `-`, and `.`.
Descriptions are bounded to 1,024 characters and reject credential,
connection, and executable query material. Entity, field, relationship,
calculated-field, measure, grain, source-reference, and deployment-binding
identities are unique across the descriptor.

## Accepted YAML subset and bounds

The loader accepts mappings, sequences, strings, null, booleans, integers,
and finite floats. Scalar resolution follows JSON conventions: only lowercase
`true`, `false`, and `null` receive those types. Timestamp-like values and YAML
1.1 words such as `yes` and `on` remain strings. Comments and bounded aliases
are allowed, but aliases are expanded before semantic validation.

The default limits are 1 MiB UTF-8 input, 65,536 parser events, 32,768 nodes,
64 nesting levels, 4,096 characters per scalar, 16,384 entries per collection,
128 aliases, and 65,536 expanded node visits. Parsing rejects duplicate or
non-string keys, merge keys, custom/object tags, cyclic aliases, excessive
alias expansion, unsupported scalar tags, and non-finite numbers before object
construction. Includes, interpolation, templates, macros, and external I/O are
not supported.

## References and calculated fields

Relationship targets must exist. `sourceFields` must belong to the containing
entity, `targetFields` must belong to the target entity, and both lists must
have equal length. Measure fields, grain entities, and grain attributes must
resolve uniquely. Source references and deployment bindings must match
`spec.source.sourceId`.

Calculated fields use the governed expression tree with `field`, `const`,
`add`, `sub`, `mul`, and `div`. Their `requires` list must exactly match field
leaves; output type is inferred; calculated fields cannot compose; and fields
marked `pii` cannot feed a calculated field. No SQL or other executable
expression text is accepted.

Deployment bindings contain references only. Supported schemes are `env:`,
`vault:`, and `file:`. Inline endpoints, credentials, tokens, and resolved
values are rejected and never echoed in diagnostics.

## Diagnostics and lifecycle

Diagnostics have a stable code, normalized `$` path, optional one-based line
and column, and a controlled message. At most 100 issues are returned;
`issue_count` and `truncated` report omitted issues. Rejected scalar values and
raw PyYAML/Pydantic exceptions are never returned.

`SemanticAssemblyAuthoringLoader` parses and validates without persistence.
`lower_authoring` receives `draft_id` and `author_reference` from the trusted
host, derives assertion identities, and creates a revision-zero `draft` with
manual provenance and pending assertions. Review, approval, publication, and
activation still use the normal lifecycle.

The optional Admin service exposes `validate_authoring` with `bundle:validate`
permission and `import_authoring` with `assembly:write` permission plus the
Author role. Both require authorized source scope. Validation never touches the
draft store; import persists only through `create_draft`.

## Export guarantees

Authoring export is deterministic block-style YAML. Identity-keyed collections
are sorted, aliases are disabled, and strings are explicitly quoted so
ambiguous values retain their type. Parse/export/parse preserves normalized
semantics and lowering payload hashes. Export omits lifecycle and secret data;
a reviewed, modified, or otherwise non-representable draft is rejected rather
than exported lossily.

## Rejected features

The schema rejects unknown members and all lifecycle-owned fields, including
assertion IDs, provenance, review state/bindings, draft revision, approver or
publisher identities, audit records, semantic fingerprints, activation and
supersession state, executable query text, and resolved credentials.

## Next steps

- Run `python demo/run/demo_deterministic.py` to validate, import, review,
  approve, publish, and activate the complete example without external
  services.
- See [Troubleshooting](../operations/troubleshooting.md) for diagnostic and
  import failures.