"""Shared LLM pricing table and cost calculation utilities."""

MODEL_PRICING: dict[str, dict[str, float]] = {
    'gemini-3-flash':        {'input': 0.50,  'output': 3.00,  'cached': 0.05},
    'gemini-3.1-flash-lite': {'input': 0.25,  'output': 1.50,  'cached': 0.025},
    'gemini-2.5-flash':      {'input': 0.30,  'output': 2.50,  'cached': 0.03},
    'gemini-2.5-flash-lite': {'input': 0.10,  'output': 0.40,  'cached': 0.01},
    'gemini-2.0-flash':      {'input': 0.10,  'output': 0.40,  'cached': 0.025},
    'gemini-2.0-flash-lite': {'input': 0.075, 'output': 0.30,  'cached': 0.019},
    'gemini-1.5-flash':      {'input': 0.075, 'output': 0.30,  'cached': 0.019},
    'default':               {'input': 0.30,  'output': 2.50,  'cached': 0.03},
}


def get_pricing(model: str) -> dict[str, float]:
    for key, price in MODEL_PRICING.items():
        if key != 'default' and key in model:
            return price
    return MODEL_PRICING['default']


def calc_cost(input_tokens: int, output_tokens: int, cached_tokens: int, model: str) -> float:
    p = get_pricing(model)
    return (
        (input_tokens / 1_000_000) * p['input'] +
        (output_tokens / 1_000_000) * p['output'] +
        (cached_tokens / 1_000_000) * p['cached']
    )
