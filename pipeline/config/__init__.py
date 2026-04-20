# CODEX TASK C6 â€” implement this file
# Load config/llm.yaml into validated Pydantic models.
# See DESIGN.md Section 7 for the config structure.
# Expose: load_llm_config() -> LLMConfig

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ProviderModelConfig(BaseModel):
    high: str | None = None
    medium: str | None = None
    low: str | None = None


class ProviderConfig(BaseModel):
    api_key_env: str | None = None
    models: ProviderModelConfig = Field(default_factory=ProviderModelConfig)


class TierConfig(BaseModel):
    primary: str
    fallbacks: list[str] = Field(default_factory=list)


class EmbeddingConfig(BaseModel):
    provider: str
    model: str
    dimensions: int = 1536


class ExperimentalLocalConfig(BaseModel):
    enabled: bool = False
    base_url: str = "http://localhost:1234/v1"
    api_key: str = "dummy"
    model: str = ""
    degraded_marker: bool = True


class ExperimentalConfig(BaseModel):
    local: ExperimentalLocalConfig = Field(default_factory=ExperimentalLocalConfig)


class LLMConfig(BaseModel):
    providers: dict[str, ProviderConfig]
    tiers: dict[str, TierConfig]
    embeddings: dict[str, EmbeddingConfig]
    experimental: ExperimentalConfig = Field(default_factory=ExperimentalConfig)


def load_llm_config(path: Path | None = None) -> LLMConfig:
    config_path = path if path is not None else Path(__file__).with_name("llm.yaml")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data: dict[str, Any]
    if isinstance(raw, dict):
        data = raw
    else:
        data = {}
    return LLMConfig.model_validate(data)
