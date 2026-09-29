"""Per-model API prices, and the real cost of one call from its token usage."""

# USD per million tokens: (input, output, cache-read multiplier). Writing the 5-minute prompt
# cache costs 1.25x input on every model. Source: platform.claude.com/docs/en/about-claude/pricing
# (checked 2026-09-29; Sonnet 5's $2/$10 launch price became its standard price).
MODEL_PRICING = {
    "claude-sonnet-5":   (2.00, 10.00, 0.10),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-opus-5":     (5.00, 25.00, 0.10),
    "claude-opus-5-5":   (4.00, 20.00, 0.05),
    "claude-fable-5":    (10.00, 50.00, 0.10),
    "claude-fable-5-1":  (10.00, 50.00, 0.025),
    "claude-sonnet-4-6": (3.00, 15.00, 0.10),
    "claude-opus-4-8":   (5.00, 25.00, 0.10),
}
# For a --model not listed above; the CLI warns that its costs are a guess.
DEFAULT_PRICING = (3.00, 15.00, 0.10)
CACHE_WRITE_MULTIPLIER = 1.25


def compute_cost(model: str, usage) -> float:
    """Real USD cost of one API call, from the response's actual token usage."""
    input_rate, output_rate, cache_read_rate = MODEL_PRICING.get(model, DEFAULT_PRICING)
    base_in = getattr(usage, "input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    return (
        base_in * input_rate
        + cache_write * input_rate * CACHE_WRITE_MULTIPLIER
        + cache_read * input_rate * cache_read_rate
        + out * output_rate
    ) / 1_000_000
