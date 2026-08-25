## Context

The core currently assembles an authorized semantic context and sends a user prompt plus JSON context through `ModelInvocationRequest`. It has no explicit contract for system/developer instructions, safety rules, output schema, or instruction provenance. Without this boundary, each vendor provider could invent its own system prompt and accidentally become responsible for governance semantics.

## Goals / Non-Goals

**Goals:**

- Define an immutable, versioned provider-neutral `ModelInstructionBundle`.
- Separate trusted system instructions and safety constraints from the untrusted user prompt.
- Bind instructions to authorized Semantic View, model bundle, policy, tenant scope, and output schema references through safe fingerprints.
- Ensure instructions are bounded, canonical, serializable, and free of credentials, physical queries, hidden policy rules, and executable code.
- Let provider packages map the bundle to vendor-specific system/developer messages without changing its semantics.
- Include instruction identity in model invocation and workflow/evaluation evidence.

**Non-Goals:**

- OpenAI, Anthropic, LangChain, or other vendor message formats.
- Prompt optimization, retrieval, vector search, or conversation transcript storage.
- Moving authorization decisions into prompt construction.
- Guaranteeing that a model follows instructions; output must still pass IntentResolver and IR/governance gates.
- Public exposure of internal instruction models through `nl2data`.

## Decisions

### Core owns semantic instruction content

The core assembles provider-neutral instruction sections from runtime policy and authorized semantic context: role, allowed behavior, output contract, safety constraints, and bounded semantic context. A provider only serializes these sections into its vendor request format. This prevents vendor-specific prompts from becoming a second governance engine.

### User prompt remains separate

`ModelInvocationRequest.prompt` remains the caller's user content. The instruction bundle is a separate field/reference and is never concatenated into the user prompt. Providers must preserve this separation when mapping to system/developer/user messages so user text cannot override system constraints through formatting.

### Fingerprint references, not raw security claims

The bundle stores safe bounded text and opaque fingerprints for tenant scope, policy, resolved view, model bundle, and output schema. Raw tenant IDs, principal claims, credentials, hidden policy logic, physical bindings, SQL/MQL, and native objects are excluded. The fingerprint covers all instruction and security inputs.

### Strict versioned output contract

The bundle identifies an output schema/version and allowed response mode. It does not contain vendor JSON schema objects by default; providers translate the canonical output contract into vendor schema declarations. Unsupported output modes fail closed rather than silently falling back to free-form text.

### No provider-level retry

The provider receives one validated bundle and performs one bounded call. Resolver/workflow owns timeout, retry, cancellation, and model response validation. This keeps fake and real providers behaviorally interchangeable.

## Risks / Trade-offs

- [System instructions contain sensitive policy text] → Use safe reason codes and references; exclude hidden policy internals and raw identity claims.
- [Vendor mapping loses constraints] → Require provider conformance tests that inspect message separation and schema mapping.
- [Instruction version drift] → Include bundle/version/schema fingerprints in model and workflow evidence and reject incompatible versions.
- [Prompt injection in user content] → Keep user prompt separate, bound it, and validate model output independently before IR construction.
- [Prompt text is treated as authorization] → State explicitly that instructions are guidance; policy/governance gates remain authoritative.

## Migration Plan

1. Add instruction bundle models, assembly, serialization, fingerprinting, and validation behind the existing AI boundary.
2. Extend model invocation composition to carry the validated bundle or its safe reference while preserving current callers through a deterministic default bundle.
3. Add workflow/evaluation evidence and tests for instruction identity and leakage prevention.
4. Update `add-openai-model-provider` to consume the bundle and map it to OpenAI messages.
5. Roll back by using the existing prompt/context request path with the default safe instruction bundle; no persisted raw prompt migration is required.

## Open Questions

- Whether instruction sections should be represented as typed sections or one canonical system text plus metadata.
- Which output schema/version should be the first stable contract for intent resolution.
- Whether provider packages need a capability for separate system/developer messages or can always use one canonical instruction channel.
