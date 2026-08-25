"""OpenAI structured-output provider for ``nl2data-core``.

An independent optional distribution implementing the provider-neutral
``ModelProvider`` contract.  The OpenAI SDK is never imported at package
import time; clients are constructed lazily on first generation from
injected credentials or a client factory, so core imports and capability
inspection stay fully offline.
"""

from __future__ import annotations

from .config import OpenAIProviderConfig
from .provider import OpenAIModelProvider

__all__ = ["OpenAIProviderConfig", "OpenAIModelProvider"]
