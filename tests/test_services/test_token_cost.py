"""LLM token/cost accounting tests."""

from app.services.llm import usage_cost_usd, reset_token_usage, get_token_usage, _record_usage


class _Usage:
    def __init__(self, prompt, completion, cached=0):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = prompt + completion
        self.prompt_tokens_details = type("D", (), {"cached_tokens": cached})()


def test_usage_cost_uses_configured_rates():
    # gpt-5.5 defaults: $5/M in, $30/M out.
    cost = usage_cost_usd({"prompt_tokens": 10_000, "cached_input_tokens": 0, "completion_tokens": 2_000})
    assert abs(cost - (10_000 / 1e6 * 5 + 2_000 / 1e6 * 30)) < 1e-9


def test_cached_input_billed_at_cheap_rate():
    full = usage_cost_usd({"prompt_tokens": 10_000, "cached_input_tokens": 0, "completion_tokens": 0})
    half_cached = usage_cost_usd({"prompt_tokens": 10_000, "cached_input_tokens": 5_000, "completion_tokens": 0})
    assert half_cached < full  # caching must reduce cost


def test_token_usage_accumulates_across_calls():
    reset_token_usage()
    _record_usage("structured", "gpt-5.5", _Usage(1000, 200))
    _record_usage("completion", "gpt-5.5", _Usage(3000, 500, cached=1000))
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
