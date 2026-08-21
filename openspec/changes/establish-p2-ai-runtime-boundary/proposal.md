## Why

P1 can execute a governed query when given a structured Semantic Query Plan, but the system has no controlled boundary for turning a user's natural-language request into structured intent. Without that boundary, future AI integration would risk coupling the core to one provider, allowing model output to bypass semantic validation, or treating prompts and model responses as trusted execution inputs.

P2.1 establishes the smallest provider-independent AI runtime surface so later Memory, multi-turn workflow, HTTP, and additional adapters can consume deterministic structured intent safely.

## What Changes

- Add a provider-neutral `ModelProvider` contract for bounded asynchronous structured generation.
- Add immutable request, model invocation, structured intent, clarification, and model error contracts with safe metadata and fingerprints.
- Add a deterministic fake provider for unit, integration, and conformance tests without network calls.
- Add an intent-resolution boundary that accepts natural-language requests but emits only validated structured intent or clarification-required results.
- Define the handoff from structured intent to the existing Semantic Query Plan builder without allowing raw SQL, MQL, prompts, or provider objects into the plan.
- Add prompt/context assembly rules that minimize and redact context before provider invocation.
- Add model timeout, retry, token/output bounds, provider capability checks, and safe error normalization.
- Add an AI evaluation skeleton for deterministic intent cases, ambiguity cases, malformed provider output, timeout, and prompt-injection resistance.
- Defer provider-specific integrations, production prompt catalogs, autonomous agent loops, answer summarization, Memory, HTTP, and GitHub Actions to later changes.

## Capabilities

### New Capabilities

- `model-provider-boundary`: Provider-independent, bounded, asynchronous structured model invocation and safe model errors.
- `structured-intent-resolution`: Natural-language request resolution into validated intent or clarification without direct executable query output.
- `ai-evaluation-foundation`: Deterministic fake-provider cases, protected evidence, mandatory safety assertions, and repeatable intent evaluation.

### Modified Capabilities

None.

## Impact

- Adds a new internal AI/runtime package behind the existing public `nl2data` boundary.
- Adds structured intent and clarification contracts that the future workflow runtime and Memory layer can consume.
- Adds no mandatory vendor SDK, hosted model, network service, or provider credential.
- Extends configuration with bounded model invocation settings while preserving provider-neutral core behavior.
- Adds unit, contract, security, and evaluation tests for safe model boundaries.