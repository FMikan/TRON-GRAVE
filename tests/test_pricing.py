import unittest
from types import SimpleNamespace

from extractor.pricing import MODEL_PRICING, compute_cost


def usage(inp=0, out=0, write=0, read=0):
    return SimpleNamespace(input_tokens=inp, output_tokens=out,
                           cache_creation_input_tokens=write, cache_read_input_tokens=read)


class PricingTests(unittest.TestCase):
    def test_cache_reads_use_each_models_own_rate(self):
        million_reads = usage(read=1_000_000)
        self.assertAlmostEqual(compute_cost("claude-opus-5-5", million_reads), 0.20)
        self.assertAlmostEqual(compute_cost("claude-fable-5-1", million_reads), 0.25)
        self.assertAlmostEqual(compute_cost("claude-sonnet-5-5", million_reads), 0.20)
        self.assertAlmostEqual(compute_cost("claude-opus-5", million_reads), 0.50)

    def test_sonnet_5_keeps_its_standard_2_10_price(self):
        self.assertAlmostEqual(compute_cost("claude-sonnet-5", usage(inp=1_000_000, out=1_000_000)), 12.0)

    def test_cache_writes_cost_1_25x_input(self):
        self.assertAlmostEqual(compute_cost("claude-opus-5-5", usage(write=1_000_000)), 5.0)

    def test_unknown_model_uses_the_fallback(self):
        self.assertNotIn("claude-haiku-9", MODEL_PRICING)
        self.assertAlmostEqual(compute_cost("claude-haiku-9", usage(inp=1_000_000)), 3.0)
