"""Security tests: the OpenAI provider boundary never loads or leaks the SDK.

Two layers of defense for the sibling ``nl2data-openai`` distribution:

1. Import boundary: neither the core packages (``nl2data``, ``nl2data_core``)
   nor the sibling package import the ``openai`` SDK at module level.  The
   SDK is only ever reached lazily through ``importlib`` at client build
   time, so core imports and capability inspection stay fully offline.
2. Credential/data boundary: API keys, authorization headers, endpoint
   details, raw vendor responses, and vendor exception text never appear in
   core models, request metadata, error records, or fingerprints.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest
from tests.provider.fake_openai import (
    AuthenticationError,
    BadRequestError,
    FakeOpenAIClient,
    fake_response,
)

from nl2data_core.ai.errors import (
    ModelErrorCode,
    ModelErrorRecord,
    ModelInvocationError,
    normalize_model_error,
)
from nl2data_core.ai.models import ModelInvocationRequest
from nl2data_openai.client import build_openai_client
from nl2data_openai.config import OpenAIProviderConfig
from nl2data_openai.provider import OpenAIModelProvider

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
OPENAI_SRC_ROOT = (
    Path(__file__).resolve().parents[2] / "packages" / "nl2data-openai" / "src"
)

#: Credential material that must never cross any boundary.
API_KEY = "sk-security-boundary-test-0123456789"
ENDPOINT = "https://internal-secret-endpoint.example.com/v1"


def config(**overrides) -> OpenAIProviderConfig:
    values = {"model_name": "gpt-4o-mini"}
    values.update(overrides)
    return OpenAIProviderConfig(**values)


def request(request_id: str = "r1", **overrides) -> ModelInvocationRequest:
    values = {"request_id": request_id, "prompt": "show orders"}
    values.update(overrides)
    return ModelInvocationRequest(**values)


def _imported_names(module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


class TestStaticOpenAIImportBoundary:
    def test_sibling_package_never_imports_the_sdk(self) -> None:
        """The SDK may only be reached via importlib strings, never imports."""
        offenders: list[str] = []
        for module_path in OPENAI_SRC_ROOT.rglob("*.py"):
            if "openai" in _imported_names(module_path):
                offenders.append(str(module_path.relative_to(OPENAI_SRC_ROOT)))
        assert offenders == [], f"module-level openai imports found: {offenders}"

    def test_core_sources_never_import_the_sdk(self) -> None:
        offenders: list[str] = []
        for module_path in SRC_ROOT.rglob("*.py"):
            if "openai" in _imported_names(module_path):
                offenders.append(str(module_path.relative_to(SRC_ROOT)))
        assert offenders == [], f"core openai imports found: {offenders}"


class TestDynamicOpenAIImportBoundary:
    def test_importing_the_sibling_package_loads_no_sdk(self) -> None:
        import nl2data_openai  # noqa: F401
        import nl2data_openai.client  # noqa: F401
        import nl2data_openai.config  # noqa: F401
        import nl2data_openai.live_evaluation  # noqa: F401
        import nl2data_openai.mapping  # noqa: F401
        import nl2data_openai.provider  # noqa: F401

        loaded = {name.split(".")[0] for name in sys.modules}
        assert "openai" not in loaded, "openai loaded with the sibling package"

    def test_importing_core_and_public_api_loads_no_sdk(self) -> None:
        import nl2data  # noqa: F401
        import nl2data_core.ai  # noqa: F401

        loaded = {name.split(".")[0] for name in sys.modules}
        assert "openai" not in loaded, "openai loaded with the core packages"

    def test_capabilities_and_lifecycle_are_fully_offline(self) -> None:
        provider = OpenAIModelProvider(config())
        assert provider.capabilities().provider_name == "openai"
        assert provider.call_count == 0
        # Closing a provider that never built a client must be a no-op.
        import asyncio

        asyncio.run(provider.close())


class TestCredentialBoundary:
    async def test_api_key_never_enters_request_params(self, monkeypatch) -> None:
        fake = FakeOpenAIClient([fake_response('{"intent": {"source_id": "s"}}')])
        captured: dict[str, str] = {}

        def fake_build(cfg: OpenAIProviderConfig, *, api_key: str):
            captured["api_key"] = api_key
            return fake

        monkeypatch.setattr("nl2data_openai.provider.build_openai_client", fake_build)
        provider = OpenAIModelProvider(
            config(), api_key_resolver=lambda: API_KEY
        )
        response = await provider.generate(request())
        assert captured["api_key"] == API_KEY
        assert response.request_id == "r1"
        for call in fake.chat.completions.calls:
            assert "api_key" not in call
            assert "Authorization" not in call
            assert API_KEY not in json.dumps(call)

    async def test_api_key_never_enters_error_records(self) -> None:
        fake = FakeOpenAIClient(
            [AuthenticationError(f"invalid credentials for {API_KEY}")]
        )
        provider = OpenAIModelProvider(
            config(),
            api_key_resolver=lambda: API_KEY,
            client_factory=lambda: fake,
        )
        with pytest.raises(ModelInvocationError) as excinfo:
            await provider.generate(request())
        record = excinfo.value.to_record()
        assert record.code == ModelErrorCode.INVALID_REQUEST
        assert API_KEY not in str(excinfo.value)
        assert API_KEY not in json.dumps(record.safe_dump())

    def test_config_has_no_credential_field(self) -> None:
        cfg = config()
        assert not hasattr(cfg, "api_key")
        dump = json.dumps(cfg.safe_dump())
        assert API_KEY not in dump
        assert "api_key" not in dump
        assert cfg.fingerprint.startswith("sha256:")

    async def test_endpoint_details_never_cross_the_boundary(self) -> None:
        fake = FakeOpenAIClient([BadRequestError(f"request failed at {ENDPOINT}")])
        provider = OpenAIModelProvider(
            config(base_url=ENDPOINT, organization="org-secret-1"),
            client_factory=lambda: fake,
        )
        capabilities = provider.capabilities().model_dump_json()
        assert ENDPOINT not in capabilities
        assert "org-secret-1" not in capabilities
        with pytest.raises(ModelInvocationError) as excinfo:
            await provider.generate(request())
        record = excinfo.value.to_record()
        assert record.code == ModelErrorCode.INVALID_REQUEST
        assert ENDPOINT not in json.dumps(record.safe_dump())
        assert ENDPOINT not in str(excinfo.value)

    async def test_raw_responses_never_cross_the_boundary(self) -> None:
        # Refusal text and raw content must not leak into error records.
        fake = FakeOpenAIClient(
            [
                fake_response("", refusal=f"I refuse to answer {API_KEY}"),
                fake_response(json.dumps({"sql": f"SELECT 1 -- {API_KEY}"})),
            ]
        )
        provider = OpenAIModelProvider(config(), client_factory=lambda: fake)
        for _ in range(2):
            with pytest.raises(ModelInvocationError) as excinfo:
                await provider.generate(request())
            record = excinfo.value.to_record()
            assert API_KEY not in json.dumps(record.safe_dump())
            assert API_KEY not in str(excinfo.value)

    async def test_vendor_exceptions_never_cross_the_boundary(self) -> None:
        fake = FakeOpenAIClient([RuntimeError(f"boom at {ENDPOINT} key {API_KEY}")])
        provider = OpenAIModelProvider(config(), client_factory=lambda: fake)
        with pytest.raises(ModelInvocationError) as excinfo:
            await provider.generate(request())
        record = excinfo.value.to_record()
        assert record.code == ModelErrorCode.UNKNOWN_MODEL_ERROR
        assert record.cause_type == "RuntimeError"
        dump = json.dumps(record.safe_dump())
        assert API_KEY not in dump
        assert ENDPOINT not in dump

    async def test_successful_response_carries_only_the_envelope(self) -> None:
        content = json.dumps(
            {"intent": {"source_id": "sales"}, "clarification": None, "alternatives": None}
        )
        fake = FakeOpenAIClient()
        raw = fake_response(content)
        raw.secret_header = f"Bearer {API_KEY}"  # type: ignore[attr-defined]
        raw.system_fingerprint = "fp_raw_vendor_data"  # type: ignore[attr-defined]
        fake.chat.completions._responses.append(raw)
        provider = OpenAIModelProvider(config(), client_factory=lambda: fake)
        response = await provider.generate(request())
        # Null envelope placeholders are normalized to absent keys so the
        # resolver's presence-based semantics stay intact.
        assert set(response.content) == {"intent"}
        dump = json.dumps(response.model_dump())
        assert API_KEY not in dump
        assert "fp_raw_vendor_data" not in dump

    def test_normalize_model_error_redacts_unknown_messages(self) -> None:
        record = normalize_model_error(RuntimeError(f"leak {API_KEY} {ENDPOINT}"))
        assert isinstance(record, ModelErrorRecord)
        dump = json.dumps(record.safe_dump())
        assert API_KEY not in dump
        assert ENDPOINT not in dump

    async def test_build_client_failure_never_mentions_credentials(self, monkeypatch) -> None:
        monkeypatch.setattr("nl2data_openai.client.driver_available", lambda: False)
        with pytest.raises(ModelInvocationError) as excinfo:
            build_openai_client(config(base_url=ENDPOINT), api_key=API_KEY)
        record = excinfo.value.to_record()
        assert record.code == ModelErrorCode.PROVIDER_UNAVAILABLE
        dump = json.dumps(record.safe_dump())
        assert API_KEY not in dump
        assert ENDPOINT not in dump
