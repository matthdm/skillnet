from __future__ import annotations

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

# (input_$/M, output_$/M) — Anthropic public pricing
_COST_TABLE: dict[str, tuple[float, float]] = {
    "opus":   (15.0, 75.0),
    "sonnet": (3.0,  15.0),
    "haiku":  (0.25, 1.25),
}


class TokenUsageHandler(BaseCallbackHandler):
    """Accumulates token usage from on_llm_end for a single LLM invocation."""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0

    def on_llm_end(self, response: LLMResult, **kwargs: object) -> None:
        usage = self._extract_usage(response)
        self.input_tokens += int(usage.get("input_tokens", usage.get("prompt_tokens", 0)))
        self.output_tokens += int(usage.get("output_tokens", usage.get("completion_tokens", 0)))

    # on_chat_model_end fires in newer LangChain versions for ChatModels
    on_chat_model_end = on_llm_end

    @staticmethod
    def _extract_usage(response: LLMResult) -> dict:
        llm_out = response.llm_output or {}
        # Anthropic standard: llm_output["usage"]
        if llm_out.get("usage"):
            return llm_out["usage"]
        # OpenAI-compat wrapper: llm_output["token_usage"]
        if llm_out.get("token_usage"):
            return llm_out["token_usage"]
        # Newer langchain_anthropic: generation_info["usage_metadata"]
        try:
            gen_info = response.generations[0][0].generation_info or {}
            for key in ("usage_metadata", "usage"):
                if gen_info.get(key):
                    return gen_info[key]
        except (IndexError, AttributeError):
            pass
        return {}


def estimate_cost(provider_label: str | None, input_tokens: int, output_tokens: int) -> float:
    label = (provider_label or "").lower()
    for key, (in_rate, out_rate) in _COST_TABLE.items():
        if key in label:
            return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
    return 0.0
