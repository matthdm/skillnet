from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache
from pathlib import Path

from langchain_anthropic import ChatAnthropic
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from config import LLMConfig, load_llm_config

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "llm.yaml"


class LLMTier(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class LLMRouter:
    """
    Single entrypoint for all LLM access in the pipeline.
    Nodes call router.get(tier) and receive a BaseChatModel with
    fallbacks pre-wired. Never instantiate providers directly in nodes.
    """

    def __init__(self, config: LLMConfig | None = None) -> None:
        self._config = config or load_llm_config(_CONFIG_PATH)
        self._model_cache: dict[str, BaseChatModel] = {}

    @property
    def degraded_mode_enabled(self) -> bool:
        return self._config.experimental.local.enabled

    def get(self, tier: LLMTier) -> BaseChatModel:
        """Return a BaseChatModel for the given tier with fallbacks attached."""
        tier_config = self._config.tiers[tier.value]
        primary = self._build_model(tier_config.primary)
        fallbacks = [self._build_model(ref) for ref in tier_config.fallbacks]

        if self._config.experimental.local.enabled:
            fallbacks.append(self._build_local())

        if fallbacks:
            return primary.with_fallbacks(fallbacks)
        return primary

    def get_embeddings(self) -> Embeddings:
        """Return the configured embeddings implementation."""
        emb = self._config.embeddings.get("primary")
        if emb is None:
            raise ValueError("No primary embeddings config found in llm.yaml")

        if emb.provider == "openai":
            provider_cfg = self._config.providers.get("openai")
            api_key = os.environ.get(provider_cfg.api_key_env, "") if provider_cfg else ""
            return OpenAIEmbeddings(model=emb.model, api_key=api_key, dimensions=emb.dimensions)

        raise ValueError(f"Unsupported embeddings provider: {emb.provider!r}")

    def provider_label(self, tier: LLMTier) -> str:
        """Human-readable label for the primary provider of a tier, for provider_log."""
        return self._config.tiers[tier.value].primary

    # ------------------------------------------------------------------ #
    # Private helpers                                                       #
    # ------------------------------------------------------------------ #

    def _build_model(self, ref: str) -> BaseChatModel:
        """Build a model from a provider ref string like 'anthropic/high'."""
        if ref in self._model_cache:
            return self._model_cache[ref]

        provider_name, tier_name = ref.split("/", 1)
        provider_cfg = self._config.providers.get(provider_name)
        if provider_cfg is None:
            raise ValueError(f"Unknown provider: {provider_name!r}")

        model_name = getattr(provider_cfg.models, tier_name, None)
        if model_name is None:
            raise ValueError(f"No model configured for {ref!r}")

        api_key = os.environ.get(provider_cfg.api_key_env or "", "")

        if provider_name == "anthropic":
            model = ChatAnthropic(model=model_name, api_key=api_key)
        elif provider_name == "openai":
            model = ChatOpenAI(model=model_name, api_key=api_key)
        else:
            raise ValueError(f"Unsupported provider type: {provider_name!r}")

        self._model_cache[ref] = model
        return model

    def _build_local(self) -> BaseChatModel:
        """Build the experimental local model. Only called when degraded_mode_enabled."""
        cfg = self._config.experimental.local
        return ChatOpenAI(
            model=cfg.model,
            api_key=cfg.api_key,
            base_url=cfg.base_url,
        )
