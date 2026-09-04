"""
The cache-hit log is the only signal that prompt caching is actually working —
a cache that never hits looks identical to one that does, except on the bill.
"""
import logging
from types import SimpleNamespace

from llm_client import LLMClient


def log_for(usage, cache_system=True, caplog=None):
    with caplog.at_level(logging.INFO, logger="llm_client"):
        LLMClient._log_usage(SimpleNamespace(usage=usage), cache_system)
    return " ".join(r.message for r in caplog.records)


class TestUsageLogging:
    def test_reports_cache_hits(self, caplog):
        usage = SimpleNamespace(prompt_tokens=5000, completion_tokens=1200,
                                cache_read_input_tokens=3700,
                                cache_creation_input_tokens=0)
        out = log_for(usage, caplog=caplog)
        assert "cache_read=3700" in out
        assert "cache_write=0" in out

    def test_zero_reads_are_called_out(self, caplog):
        """A silently-missing cache is the failure this log exists to catch."""
        usage = SimpleNamespace(prompt_tokens=5000, completion_tokens=1200,
                                cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        assert "no cache activity" in log_for(usage, caplog=caplog)

    def test_first_call_writing_the_cache_is_not_flagged(self, caplog):
        usage = SimpleNamespace(prompt_tokens=5000, completion_tokens=1200,
                                cache_read_input_tokens=0,
                                cache_creation_input_tokens=3700)
        out = log_for(usage, caplog=caplog)
        assert "cache_write=3700" in out
        assert "no cache activity" not in out

    def test_underscore_prefixed_counters_are_read(self, caplog):
        """litellm renames these between versions."""
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=10,
                                _cache_read_input_tokens=80,
                                _cache_creation_input_tokens=0)
        assert "cache_read=80" in log_for(usage, caplog=caplog)

    def test_a_real_zero_is_not_replaced_by_the_fallback(self, caplog):
        """`or` chaining here would discard a legitimate 0 for the alias."""
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=10,
                                cache_read_input_tokens=0,
                                _cache_read_input_tokens=999,
                                cache_creation_input_tokens=5)
        assert "cache_read=0" in log_for(usage, caplog=caplog)

    def test_cache_counters_omitted_when_not_caching(self, caplog):
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=10)
        out = log_for(usage, cache_system=False, caplog=caplog)
        assert "tokens in=100 out=10" in out
        assert "cache_read" not in out

    def test_missing_usage_does_not_raise(self, caplog):
        LLMClient._log_usage(SimpleNamespace(), True)
        LLMClient._log_usage(SimpleNamespace(usage=None), True)
