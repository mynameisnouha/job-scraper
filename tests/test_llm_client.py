from llm_client import RateLimiter


class TestRateLimiter:
    def test_init(self):
        rl = RateLimiter(max_rpm=10)
        assert rl.max_rpm == 10
        assert rl.tokens == 10

    def test_init_zero_rpm(self):
        rl = RateLimiter(max_rpm=0)
        assert rl.max_rpm == 0
        assert rl.tokens == 0

    def test_acquire_immediate(self):
        """Should not block when tokens are available."""
        rl = RateLimiter(max_rpm=60)
        rl.acquire()
        assert rl.tokens < 60


class TestLLMClientImports:
    def test_llm_client_import(self):
        from llm_client import LLMClient
        assert LLMClient is not None

    def test_primary_client_exists(self):
        from llm_client import primary_client
        assert primary_client is not None
