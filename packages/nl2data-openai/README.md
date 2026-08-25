# nl2data-openai

An optional OpenAI structured-output provider for
[nl2data-core](https://github.com/emmansun/nl2data-core). It implements the
provider-neutral asynchronous `ModelProvider` contract: bounded
`ModelInvocationRequest` in, typed `ModelResponse`/normalized
`ModelInvocationError` out, with the OpenAI SDK isolated to this package.

The core import boundary never loads the OpenAI SDK; this package imports
it **lazily** at client build time — never at import, construction, or
capability inspection.

## Install

```bash
pip install nl2data-openai
```

Requires Python 3.11+, `nl2data-core>=0.1.0`, and `openai>=1.40,<3`.

From a source checkout:

```bash
pip install -e ".[dev]"                 # from the repository root (core)
pip install -e packages/nl2data-openai  # this package (editable)
```

## Public surface

```python
from nl2data_openai import OpenAIProviderConfig, OpenAIModelProvider
```

- `OpenAIProviderConfig` — vendor `model_name` plus bounded invocation
  settings: `max_input_chars`, `max_output_tokens`, `temperature`,
  `timeout_seconds`, optional `base_url` and `organization`. Capabilities are derived from this
  configuration **without any network call**.
- `OpenAIModelProvider` — the `ModelProvider` port implementation.
  `close()` is idempotent and never leaks native clients or exceptions.

## Credential injection

API keys never enter core models, configuration fingerprints, request
metadata, workflow state, telemetry, or errors. Inject them through one
of:

1. An `api_key_resolver` callable at provider construction, or
2. A `client_factory` at provider construction, or
3. The `OPENAI_API_KEY` environment variable — read **only when the
   client is first built**.

```python
import os

from nl2data_openai import OpenAIProviderConfig, OpenAIModelProvider

provider = OpenAIModelProvider(
    config=OpenAIProviderConfig(model_name="gpt-4o-mini"),
    api_key_resolver=lambda: os.environ["OPENAI_API_KEY"],  # host-owned
)
```

See [Secrets and live testing](../../docs/operations/secrets.md) for the
full credential-handling contract.

## Gateway compatibility

Set `base_url` to point at any OpenAI-compatible gateway (OpenAI,
Azure OpenAI-compatible endpoints, or a self-hosted proxy):

```python
OpenAIProviderConfig(
    model_name="deployed-model",
    base_url="https://your-gateway.example/v1",   # host-owned endpoint
    timeout_seconds=60,
)
```

`base_url` and `organization` are bounded configuration fields; the
endpoint itself is a host-owned setting and never part of core
configuration or evidence.

## Model selection and limits

`OpenAIProviderConfig` carries `model_name` plus bounded invocation
settings (`max_input_chars`, `max_output_tokens`, `temperature`,
`timeout_seconds`, optional `base_url`, `organization`). Provider calls
are bounded by the resolver's attempt budget; the provider performs
**exactly one vendor request per `generate()` call** — timeout, retry,
and attempt-budget policy belong to `IntentResolver`.

## Failure classification

| Condition | Normalized result |
| --- | --- |
| Authentication/configuration failure | Non-retryable `INVALID_REQUEST` |
| Timeout, connection, rate-limit, transient service error | Retryable `MODEL_TIMEOUT` / `PROVIDER_UNAVAILABLE` |

## Live testing

An opt-in live evaluation profile (`run_live_openai_evaluation` in
`nl2data_openai.live_evaluation`) runs the deterministic AI dataset
against the real provider and classifies every case as `verified`,
`unavailable`, or `skipped`. Without injected credentials/factory or
`OPENAI_API_KEY`, every case is `skipped` — default CI needs no
credentials and makes no network access.

Local run from the repository root (credentials from the environment
only — the script never writes them to disk or includes them in output):

```bash
$env:OPENAI_API_KEY = "..."          # host secret injection, never committed
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
$env:OPENAI_MODEL = "gpt-4o-mini"
$env:OPENAI_TIMEOUT_SECONDS = "60"   # optional
$env:OPENAI_LIVE_CASES = "normal-intent"  # optional, comma-separated
python scripts/run_openai_live.py
```

Exit code is 0 only when every selected case is `verified`. Evidence
carries only protected fingerprints and normalized codes.

## Rollback

Swap the provider back to the core's deterministic `FakeModelProvider`
(`nl2data_core.ai.fake` — contributor-only) at composition time to remove
the SDK dependency and network access while keeping the same resolver,
governance, and evaluation gates. No runtime migration is involved: the
provider is a composition input, so rollback is a deployment decision.

## More documentation

- [Documentation index](../../docs/README.md)
- [Adding a model provider](../../docs/development/adding-adapter-or-provider.md)
- [Secrets and live testing](../../docs/operations/secrets.md)
- [Capabilities and support](../../docs/reference/capabilities.md)
