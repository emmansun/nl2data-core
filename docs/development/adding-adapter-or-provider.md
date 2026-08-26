# Adding an Adapter or Provider

> **Reader**: adapter/provider authors — i.e. contributors who extend the
> library with a new query adapter (SQL, MongoDB, ...) or a new model
> provider (OpenAI, Anthropic, ...). **Prerequisites**:
> [Local development](local-development.md). This guide intentionally
> labels `nl2data_core` imports as **contributor-only**: application
> documentation never uses them.

## The boundary you must not bypass

The governed workflow runtime owns one explicit order for every request:

```
initialize -> memory -> intent -> plan -> validate -> compile -> guard
-> govern -> authorize -> execute -> protect -> persist -> complete
```

A new adapter or provider plugs into this order **behind the existing
gates**. It must never bypass:

- **IR validation** — executable text (SQL/MQL/shell/AST/driver-shaped
  output) is produced only by the compiler, never by an adapter or
  provider.
- **The compiler–governance boundary** — compilers consume one immutable
  `CompilationContext` (validated IR, adapter capabilities, effective
  limits, mandatory filter obligations, view/bundle/tenant/policy
  references, physical bindings) and emit backend-neutral
  `CompilationEvidence` carrying only fingerprints.
- **Governance and authorization** — compilation alone cannot grant
  authority. Execution requires an artifact guard bound to the compiled
  artifact and an authorization issued only after governance, re-verified
  immediately before execution.
- **Result protection** — adapters return protected scalar results;
  native cursors, connections, driver-specific values, and raw payloads
  never cross the public boundary.

## Adding a query adapter

1. **Implement the adapter contract** behind the framework-neutral
   `QueryAdapter` lifecycle (parse/validate/generate/execute/close, with
   bounded capability declarations). The base package never imports your
   driver; load it lazily at client build time.
2. **Feed the existing gates**: adapter capabilities declare supported
   operations; governance consumes query facts (collections/fields/
   operations/result shape/tenant obligations); tenant profiles require
   mandatory predicates or routing evidence and fail closed when they
   cannot be enforced; result protection normalizes values conservatively
   and rejects unsupported native values with safe structured errors.
3. **Keep metadata discovery separate**: query adapters and metadata
   discoverers are separate protocols. A discoverer exposes
   `metadata_discovery_capability()` and never leaks backend-specific
   models into the common contract.
4. **Ship the driver as an optional extra** (`pyproject.toml`), exactly
   like `postgres`, `mongodb`, and `redis` today. The import-boundary
   tests (AST scans + `sys.modules` checks) enforce that no driver type
   enters the public `nl2data` API or the framework-neutral contracts.
5. **Provide conformance coverage**: reuse the deterministic suites
   (`tests/contract/test_adapter_protocol.py`,
   `tests/conformance/...`) and add a real-service profile that **skips**
   when the driver or service is unavailable — never a pass.

### Example: MongoDB adapter profile

The MongoDB adapter (`mongodb` extra, PyMongo 4.6+) is the reference
example: strict JSON wire-form specifications validated against
collection/field/operator/stage allowlists; bounded results (document,
column, byte, wall-clock); supported BSON values normalized into scalar
`ExecutionResult` rows; `find`, bounded `aggregate`, and `count_documents`
only — writes, administrative commands, JavaScript, `$where`, regex
evaluation, and wildcard projections are rejected before any driver call.

### Example: PostgreSQL adapter package (`nl2data-postgres`)

The PostgreSQL adapter is a sibling distribution (`nl2data-postgres`)
implementing both the `MetadataDiscoverer` and `QueryAdapter` ports.
`PostgresAdapterConfig` carries a host-injected DSN reference, bounded
object/field/statistics limits, and read-only pool settings. Discovery
returns core `MetadataSnapshot` values; execution uses a read-only
connection pool, statement timeout, and result bounds. The `psycopg`
driver is loaded lazily at first use and never imported at package
import time.

### Example: MongoDB adapter package (`nl2data-mongodb`)

The MongoDB adapter is a sibling distribution (`nl2data-mongodb`)
implementing both the `MetadataDiscoverer` and `QueryAdapter` ports.
`MongoAdapterConfig` carries a host-injected URI reference, bounded
collection/path/sample limits, and read-only client settings. Discovery
returns core `MetadataSnapshot` values with canonical dotted paths;
execution enforces pipeline/stage/operator validation, tenant scope,
and result bounds. The `pymongo` driver is loaded lazily at first use
and never imported at package import time. The legacy in-core
`nl2data_core.adapters.mongodb` path remains self-contained with
equivalent behavior and emits a `DeprecationWarning`; migrate to the
package.

## Adding a model provider

1. **Implement the `ModelProvider` port** (internal
   `nl2data_core.ai.protocol`): a provider-neutral asynchronous port for
   structured output. It receives a bounded invocation request
   (natural-language prompt **plus an authorized context payload**) and
   never receives database clients, credentials, or unfiltered catalog
   objects.
2. **Ship the SDK lazily**: the core never imports your SDK; your package
   imports it only at client build time — never at import, construction,
   or capability inspection.
3. **Own only the transport mapping**: map the versioned
   `ModelInstructionBundle` to your system/developer/user message channels.
   Never reconstruct governance semantics from raw context; instruction
   text and user prompt always travel as separate fields.
4. **Classify failures**: raise a normalized `ModelInvocationError` with a
   stable `ModelErrorCode`. Authentication/configuration failures are
   `INVALID_REQUEST` (non-retryable); timeout, connection, rate-limit,
   and transient service errors are `MODEL_TIMEOUT` / `PROVIDER_UNAVAILABLE`
   (retryable). Public outcomes surface these as `MODEL_INVOCATION_FAILED`
   with the internal code in `details.model_code` — internal codes are
   never public. Your provider performs exactly one vendor request per
   `generate()` call — timeout, retry, and attempt-budget policy belong
   to `IntentResolver`.
5. **Inject credentials, never store them**: API keys enter through an
   `api_key_resolver` callable, a `client_factory`, or an environment
   variable read only when the client is first built. They never enter
   core models, configuration fingerprints, request metadata, workflow
   state, telemetry, or errors.

### Example: OpenAI provider (`nl2data-openai`)

The sibling distribution implements the `ModelProvider` port with OpenAI
structured output. `OpenAIProviderConfig` carries `model_name` plus
bounded invocation settings (`max_input_chars`, `max_output_tokens`,
`temperature`, `timeout_seconds`, optional `base_url` and `organization`);
capabilities derive from configuration with no network call. See
[Secrets and live testing](../operations/secrets.md) for the live
evaluation profile.

## Verification checklist before you finish

- [ ] Deterministic unit and contract tests pass with no driver installed.
- [ ] Import-boundary tests pass: no driver/SDK type enters `nl2data` or
      the framework-neutral contracts; optional modules stay unloaded
      until first use.
- [ ] Real-service profile skips explicitly (never passes) without the
      service.
- [ ] Governance/authorization/result-protection gates are exercised, not
      bypassed.
- [ ] Documentation examples use public boundaries and placeholders; no
      credentials, raw prompts, queries, or results are persisted.
