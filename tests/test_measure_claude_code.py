"""The measurement script reads tokens and cost from `claude -p` JSON output. It must
run claude fenced (no tools, one turn) and never with permission checks skipped."""

import json

from scoring import measure_claude_code as m

SUCCESS = json.dumps({
    "type": "result", "subtype": "success", "is_error": False,
    "duration_ms": 4210, "total_cost_usd": 0.0123,
    "result": '{"passes": true}',
    "usage": {"input_tokens": 1500, "output_tokens": 220,
              "cache_read_input_tokens": 9000, "cache_creation_input_tokens": 300},
})


def test_parse_usage_reads_the_numbers():
    out = m.parse_usage(SUCCESS)
    assert out["ok"] is True
    assert out["cost_usd"] == 0.0123
    assert out["input_tokens"] == 1500
    assert out["output_tokens"] == 220
    assert out["cache_read_input_tokens"] == 9000
    assert out["cache_creation_input_tokens"] == 300
    assert out["duration_ms"] == 4210
    assert out["result"] == '{"passes": true}'


def test_parse_usage_rejects_non_json():
    assert m.parse_usage("not json")["ok"] is False


def test_parse_usage_rejects_an_error_result():
    assert m.parse_usage(json.dumps({"is_error": True, "usage": {}}))["ok"] is False


def test_argv_is_fenced():
    argv = m.claude_argv("haiku", "C:/tmp/system.txt")
    assert argv[1] == "-p"
    assert argv[argv.index("--max-turns") + 1] == "1"
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--system-prompt-file") + 1] == "C:/tmp/system.txt"
    assert argv[argv.index("--model") + 1] == "haiku"
    assert "--dangerously-skip-permissions" not in argv
