# Configuration Reference

> **Reader**: integrators and operators. **Prerequisites**:
> [Installation](../getting-started/installation.md).

## Loading configuration

Configuration loads through the public `load_config` function:

```python
from nl2data import load_config

config = load_config(
    {
        "schema_version": 1,
        "service": {"name": "example", "environment": "production"},
    }
)
```

The loader compiles defaults and supplied values into an **immutable
effective snapshot** with a deterministic configuration fingerprint.
Activation is fail-closed:

| Failure | Code | Retryable |
| --- | --- | --- |
| Unsupported schema version | `UNSUPPORTED_SCHEMA_VERSION` | No |
| Unknown field in a strict core section | `INVALID_CONFIGURATION` / `MALFORMED_CONFIGURATION` | No |
| Protected core field overridden through extensions | `PROTECTED_FIELD_OVERRIDE` | No |
| Malformed or out-of-bounds value | `MALFORMED_CONFIGURATION` | No |

The snapshot is frozen: application code cannot mutate an activated
configuration. Equivalent configurations in different key orders produce
equivalent snapshots with the same fingerprint.

## Core fields (schema version 1)

### `service` (required)

| Field | Type | Bounds | Default |
| --- | --- | --- | --- |
| `name` | string | 1–128 chars, **required** | — |
| `version` | string | 1–64 chars | `None` |
| `environment` | string | 1–64 chars | `development` |

### `runtime` (optional)

| Field | Type | Bounds | Default |
| --- | --- | --- | --- |
| `max_attempts` | int | 1–10 | 3 |
| `timeout_seconds` | float | 0–3600 | 30.0 |
| `telemetry_enabled` | bool | — | `true` |
| `max_artifact_bytes` | int | 1,024 – 1 GiB | 1,048,576 |
| `shutdown_grace_seconds` | float | 0–300 | 5.0 |

### `secrets` (optional)

Secret references — never plaintext values:

```python
{
    "schema_version": 1,
    "service": {"name": "example"},
    "secrets": {
        "openai_api_key": {"kind": "env", "name": "OPENAI_API_KEY"},
    },
}
```

- Only the reference (`kind` + `name`) is serialized; resolved plaintext
  must never be stored or emitted.
- `safe_dump()` and diagnostics output contain no plaintext secret
  values — only references or redacted markers.
- Production-safe dumping preserves references only.

### `extensions` (optional)

Arbitrary scalar key/value sections for host-specific settings:

```python
"extensions": {
    "my_host": {"values": {"feature_flag": True, "max_parallel": 4}}
}
```

Values are bounded scalars (string/int/float/bool). Protected core
fields cannot be overridden through extensions.

### `model` (optional)

Provider-agnostic model invocation settings (`ModelConfig`), defaulting
to the deterministic fake provider:

| Field | Type | Bounds | Default |
| --- | --- | --- | --- |
| `provider_name` | string | 1–64 chars | `fake` |
| `model_name` | string | 1–128 chars | `fake-model` |
| `max_input_chars` | int | 1,000–1,000,000 | 100,000 |
| `max_output_tokens` | int | 1–131,072 | 4,096 |
| `timeout_seconds` | float | 0–3600 | 30.0 |
| `max_attempts` | int | 1–10 | 3 |
| `temperature` | float | 0.0–2.0 | `None` |
| `fingerprint` | string | `sha256:<64 hex>` | computed |

The model section never carries credentials; provider secrets remain
`SecretReference` entries in `secrets`.

## Optional dependency profiles

Optional services are configured **outside** the core configuration
document, by host-owned endpoints:

| Profile | Extra | Configuration location |
| --- | --- | --- |
| SQL adapter | `sql` | `sqlglot` compiled bounded SQL; SQLite fixtures need no service |
| PostgreSQL shared state | `postgres` | `PostgreSQLStateStore` settings + `NL2DATA_POSTGRES_DSN` |
| Redis Memory | `redis` | `RedisMemoryConfig` (namespace + bounds) + connection URL |
| MongoDB adapter/discovery | `mongodb` | `ProductionDiscoveryConfig.bounds` + `NL2DATA_MONGO_URI` |
| OpenAI provider | `nl2data-openai` | `OpenAIProviderConfig` + `OPENAI_API_KEY`/`base_url` |

Host-owned endpoint/secret injection never presents vendor credentials
as core configuration fields.

## Fingerprint stability

The configuration fingerprint is deterministic: equivalent inputs loaded
in different key orders produce identical `sha256:<lowercase hex>`
identities, and secret values never enter the fingerprint. See
[Evidence and fingerprints](../architecture/evidence-and-fingerprints.md).

## Next steps

- [Error codes](error-codes.md)
- [配置参考 (简体中文)](configuration.zh-CN.md)
