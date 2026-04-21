from __future__ import annotations

# (input_$/M, output_$/M) — Anthropic public pricing
_COST_TABLE: dict[str, tuple[float, float]] = {
    "opus":   (15.0, 75.0),
    "sonnet": (3.0,  15.0),
    "haiku":  (0.25, 1.25),
}


def extract_usage(raw_message: object) -> tuple[int, int]:
    """
    Extract (input_tokens, output_tokens) from a LangChain AIMessage.
    Tries usage_metadata (standardized in LC 0.2+) then response_metadata["usage"].
    """
    if raw_message is None:
        return 0, 0
    # AIMessage.usage_metadata — standardized field (dict or UsageMetadata)
    um = getattr(raw_message, "usage_metadata", None) or {}
    inp = int(um.get("input_tokens", 0))
    out = int(um.get("output_tokens", 0))
    if inp or out:
        return inp, out
    # response_metadata["usage"] — Anthropic native via langchain_anthropic
    rm = getattr(raw_message, "response_metadata", {}) or {}
    usage = rm.get("usage", {})
    inp = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)))
    out = int(usage.get("output_tokens", usage.get("completion_tokens", 0)))
    return inp, out


def estimate_cost(provider_label: str | None, input_tokens: int, output_tokens: int) -> float:
    label = (provider_label or "").lower()
    for key, (in_rate, out_rate) in _COST_TABLE.items():
        if key in label:
            return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
    return 0.0
