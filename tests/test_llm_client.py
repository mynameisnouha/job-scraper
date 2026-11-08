from scoring.llm_client import RateLimiter


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
        from scoring.llm_client import LLMClient
        assert LLMClient is not None

    def test_primary_client_exists(self):
        from scoring.llm_client import primary_client
        assert primary_client is not None


class TestClaudeCodeModels:
    """A claude-code/<model> string runs through `claude -p`, never litellm."""

    @staticmethod
    def _client(model):
        from scoring.llm_client import LLMClient
        return LLMClient(model=model, max_rpm=1000, request_delay=0)

    def test_claude_code_model_skips_litellm(self, monkeypatch):
        from scoring import claude_code, llm_client
        seen = []
        monkeypatch.setattr(claude_code, "complete",
                            lambda prompt, system_prompt, model, response_format=None:
                            seen.append(model) or "{}")
        monkeypatch.setattr(llm_client.litellm, "completion",
                            lambda **kw: (_ for _ in ()).throw(AssertionError("litellm used")))
        assert self._client("claude-code/haiku").generate_content("p", system_prompt="s") == "{}"
        assert seen == ["haiku"]

    def test_model_override_routes_too(self, monkeypatch):
        from scoring import claude_code
        seen = []
        monkeypatch.setattr(claude_code, "complete",
                            lambda prompt, system_prompt, model, response_format=None:
                            seen.append(model) or "{}")
        client = self._client("anthropic/claude-haiku-4-5")
        client.generate_content("p", model_override="claude-code/sonnet")
        assert seen == ["sonnet"]

    def test_limit_stops_the_rest_of_generate_many(self, monkeypatch):
        from scoring import claude_code
        calls = []

        def complete(prompt, system_prompt, model, response_format=None):
            calls.append(prompt)
            if len(calls) == 2:
                raise claude_code.ClaudeCodeLimitReached("limit")
            return "ok"

        monkeypatch.setattr(claude_code, "complete", complete)
        out = self._client("claude-code/haiku").generate_many(
            {k: {"prompt": k} for k in ("a", "b", "c", "d")})
        assert len(calls) == 2
        assert list(out.values()).count(None) == 3
        assert out["a"] == "ok"
