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
measures, grains, policy templates, source references, compatibility,
deployment bindings, and a bounded `verificationPlan`.
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

## Policy templates

The bounded `spec.policies` section declares governance policy intent as
**policy template** references with typed parameters. The registry is closed
code with exactly four templates; unknown template names, unknown or missing
parameters, wrong value kinds, and out-of-bounds lists fail closed with
source-located diagnostics before any draft is created. Each declaration
accepts at most 8 parameters; each document accepts at most 64 declarations.
The section never accepts raw policy payloads, fingerprints, lifecycle state,
approval bindings, credentials, physical names, or non-scalar values.

| Template | Parameters (all required) | Identifying target |
| --- | --- | --- |
| `tenant-isolation` | `entity`, `field`, `claim` (identifiers) | entity + field |
| `row-restriction` | `entity`, `field`, `allowed_values` (bounded scalar list, 1-256) | entity + field |
| `purpose-gating` | `purposes` (identifier list, 1-16), `effect` (`allow` \| `deny`) | purposes (sorted) |
| `field-masking` | `fields` (entity.field reference list, 1-64), `replacement` (bounded non-empty string) | fields (sorted) |

Entity, field, and entity.field parameters must resolve against the declared
entities and fields. Expanded policy identities are unique per document: two
declarations that expand to the same identity conflict instead of shadowing.

Policy templates expand into ordinary pending **policy assertions** during
lowering, before review. The expanded identity is derived from the template
name and its identifying target (for example
`tenant-isolation.customers.tenant_id`); value parameters such as `claim`,
`allowed_values`, `effect`, or `replacement` change the assertion payload,
not its identity. A deterministic digest fallback keeps identities within the
identifier bound when a rendered target would exceed it. After expansion the
template form disappears: the draft carries only standard pending policy
assertions that traverse the existing review, approval, verification, and
publish gates, the canonical payload contains resolved policy semantics only
(`policy_kind` plus typed parameters, never a `template` reference), and no
fingerprint computation depends on template declarations. Export round-trips
declarations ordered by expanded identity, independently of document
presentation.

Naming note: a **policy template** is authoring sugar; a **policy assertion**
is expanded draft and bundle content; the **verification policy profile**
(`verificationPlan.policyProfile`) remains verification configuration and is
unrelated to governance policy assertions.

Expanded policies describe governance intent that is reviewed as assertions.
They are content units for review, diff, and audit — not a new runtime
decision engine: query-time enforcement continues to come from the host
`PolicyScope`, and binding policy assertions to runtime enforcement is a
deliberately deferred future change.

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

`verificationPlan` accepts policy/version, bounded deadlines, canonical
Semantic IR, fixture profile identifiers, capability requirements, smoke
assertions, and semantic contracts. It rejects supplied fingerprints,
approval bindings, statuses, evidence, runner/executor identities, SQL/MQL,
physical names, and credentials. Lowering attaches the plan to the revision-zero
draft; later plan edits use normal revision and reapproval rules.

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