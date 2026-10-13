"""LLM token/cost accounting tests."""

from app.config import get_settings
from app.services.llm import usage_cost_usd, reset_token_usage, get_token_usage, _record_usage


class _Usage:
    """Anthropic Messages usage shape: fresh input + separate cache read/write + output."""
    def __init__(self, input_tokens, output_tokens, cache_read=0, cache_write=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read
        self.cache_creation_input_tokens = cache_write


def test_usage_cost_uses_configured_rates():
    s = get_settings()
    cost = usage_cost_usd({"prompt_tokens": 10_000, "cached_input_tokens": 0, "completion_tokens": 2_000})
    expected = 10_000 / 1e6 * s.llm_price_input_per_m + 2_000 / 1e6 * s.llm_price_output_per_m
    assert abs(cost - expected) < 1e-9


def test_cached_input_billed_at_cheap_rate():
    full = usage_cost_usd({"prompt_tokens": 10_000, "cached_input_tokens": 0, "completion_tokens": 0})
    half_cached = usage_cost_usd({"prompt_tokens": 10_000, "cached_input_tokens": 5_000, "completion_tokens": 0})
    assert half_cached < full  # caching must reduce cost


def test_token_usage_accumulates_across_calls():
    reset_token_usage()
    _record_usage("structured", "claude-opus-5-5", _Usage(1000, 200))
    # 2000 fresh + 1000 cache-read = 3000 total input, 1000 of it cached.
    _record_usage("completion", "claude-opus-5-5", _Usage(2000, 500, cache_read=1000))
    u = get_token_usage()
    assert u["calls"] == 2
    assert u["prompt_tokens"] == 4000
    assert u["cached_input_tokens"] == 1000
    assert u["completion_tokens"] == 700
    assert u["cost_usd"] > 0


def test_generation_run_stats_has_cost_block(tmp_path, monkeypatch):
    import app.services.generation_artifacts as ga

    monkeypatch.setattr(ga, "_storage_root", lambda: tmp_path)
    # no runs yet -> cost block present, zeroed
    stats = ga.generation_run_stats()
    assert stats["cost"]["runs_with_cost"] == 0
