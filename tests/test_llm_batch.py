"""LLMClient.generate_many: one Message Batch, a deadline, and a per-request
synchronous fallback, so a slow or broken batch costs the discount and never a job."""

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

import config
from scoring import llm_client
from scoring.llm_client import LLMClient


class Answer(BaseModel):
    ok: bool


def _succeeded(custom_id, payload):
    message = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", input=payload)],
        usage=SimpleNamespace(input_tokens=100, output_tokens=20,
                              cache_read_input_tokens=0, cache_creation_input_tokens=0),
    )
    return SimpleNamespace(custom_id=custom_id, result=SimpleNamespace(type="succeeded", message=message))


def _failed(custom_id, kind="errored"):
    return SimpleNamespace(custom_id=custom_id, result=SimpleNamespace(type=kind))


class FakeBatches:
    def __init__(self, statuses, results, create_error=None):
        self.statuses = list(statuses)
        self._results = results
        self.create_error = create_error
        self.created = None
        self.canceled = False

    def create(self, requests):
        if self.create_error:
            raise self.create_error
        self.created = requests
        return SimpleNamespace(id="batch_1", processing_status="in_progress")

    def retrieve(self, batch_id):
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return SimpleNamespace(id=batch_id, processing_status=status)

    def cancel(self, batch_id):
        self.canceled = True
        self.statuses = ["canceling", "ended"]

    def results(self, batch_id):
        return iter(self._results)


class FakeClock:
    """time.monotonic advances only when the code sleeps."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 1)

    def time(self):
        return self.now


@pytest.fixture
def batching(monkeypatch):
    monkeypatch.setattr(config, "LLM_BATCH_ENABLED", True)
    monkeypatch.setattr(config, "LLM_BATCH_DEADLINE_MINUTES", 60)
    monkeypatch.setattr(config, "LLM_BATCH_POLL_SECONDS", 60)
    monkeypatch.setattr(llm_client, "time", FakeClock())


def _client(monkeypatch, batches, model="anthropic/claude-haiku-4-5"):
    client = LLMClient(model=model)
    monkeypatch.setattr(client, "_batch_client",
                        lambda: SimpleNamespace(messages=SimpleNamespace(batches=batches)))
    synced = []

    def fake_sync(**kwargs):
        synced.append(kwargs["prompt"])
        return f"sync:{kwargs['prompt']}"

    monkeypatch.setattr(client, "generate_content", fake_sync)
    return client, synced


REQUESTS = {
    "job/1": {"prompt": "a", "system_prompt": ["rubric", "resume"], "cache_system": True,
              "response_format": Answer, "temperature": 0.3},
    "job 2": {"prompt": "b", "response_format": Answer, "temperature": 0.0},
}


def test_requests_are_translated_to_native_batch_params(monkeypatch, batching):
    batches = FakeBatches(["ended"], [_succeeded("r0", {"ok": True}), _succeeded("r1", {"ok": False})])
    client, _ = _client(monkeypatch, batches)
    client.generate_many(REQUESTS)

    first, second = batches.created
    # Job ids can carry any character; custom_id may not.
    assert [first["custom_id"], second["custom_id"]] == ["r0", "r1"]
    params = first["params"]
    assert params["model"] == "claude-haiku-4-5"
    assert params["max_tokens"] == 8192
    assert params["temperature"] == 0.3
    assert params["messages"] == [{"role": "user", "content": "a"}]
    assert params["system"] == [
        {"type": "text", "text": "rubric", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "resume", "cache_control": {"type": "ephemeral"}},
    ]
    assert params["tools"][0]["name"] == "structured_output"
    assert params["tools"][0]["input_schema"] == Answer.model_json_schema()
    assert params["tool_choice"] == {"type": "tool", "name": "structured_output"}
    assert "system" not in second["params"]


def test_all_succeeded_returns_tool_json_without_sync_calls(monkeypatch, batching):
    batches = FakeBatches(["in_progress", "ended"],
                          [_succeeded("r1", {"ok": False}), _succeeded("r0", {"ok": True})])
    client, synced = _client(monkeypatch, batches)
    out = client.generate_many(REQUESTS)

    assert json.loads(out["job/1"]) == {"ok": True}
    assert json.loads(out["job 2"]) == {"ok": False}
    assert synced == []


def test_deadline_cancels_and_syncs_the_unfinished(monkeypatch, batching):
    batches = FakeBatches(["in_progress"], [_succeeded("r0", {"ok": True}), _failed("r1", "canceled")])
    client, synced = _client(monkeypatch, batches)
    out = client.generate_many(REQUESTS)

    assert batches.canceled
    assert json.loads(out["job/1"]) == {"ok": True}
    assert out["job 2"] == "sync:b"
    assert synced == ["b"]


def test_errored_result_is_synced(monkeypatch, batching):
    batches = FakeBatches(["ended"], [_succeeded("r0", {"ok": True}), _failed("r1")])
    client, synced = _client(monkeypatch, batches)
    out = client.generate_many(REQUESTS)
    assert synced == ["b"]
    assert out["job 2"] == "sync:b"


def test_create_failure_syncs_everything(monkeypatch, batching):
    batches = FakeBatches(["ended"], [], create_error=RuntimeError("401 invalid x-api-key"))
    client, synced = _client(monkeypatch, batches)
    out = client.generate_many(REQUESTS)
    assert synced == ["a", "b"]
    assert out == {"job/1": "sync:a", "job 2": "sync:b"}


def test_sync_fallback_failure_is_none(monkeypatch, batching):
    batches = FakeBatches(["ended"], [_failed("r0"), _failed("r1")])
    client, _ = _client(monkeypatch, batches)

    def boom(**kwargs):
        raise RuntimeError("still down")

    monkeypatch.setattr(client, "generate_content", boom)
    assert client.generate_many(REQUESTS) == {"job/1": None, "job 2": None}


def test_non_anthropic_model_never_touches_the_batch_api(monkeypatch, batching):
    client = LLMClient(model="gemini/gemini-2.5-flash")

    def no_batch():
        raise AssertionError("batch client used for a non-Anthropic model")

    monkeypatch.setattr(client, "_batch_client", no_batch)
    monkeypatch.setattr(client, "generate_content", lambda **kw: "sync")
    assert client.generate_many(REQUESTS) == {"job/1": "sync", "job 2": "sync"}


def test_batching_off_runs_synchronously(monkeypatch):
    batches = FakeBatches(["ended"], [])
    client, synced = _client(monkeypatch, batches)
    client.generate_many(REQUESTS)
    assert batches.created is None
    assert synced == ["a", "b"]
