import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from apply import mail_codes
from apply.mail_codes import extract_code, find_code

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
SINCE = NOW - timedelta(minutes=15)


def msg(frm, subject, body, date=NOW):
    return (frm, subject, body, date)


def test_six_digit_code_in_german_mail():
    assert extract_code("Ihr Code", "Ihr Bestätigungscode lautet: 482913. Danke") == "482913"


def test_verification_link_preferred_over_digits():
    body = "Your code is 123456 or click https://boards.greenhouse.io/verify?token=abc"
    assert extract_code("Verify", body) == "https://boards.greenhouse.io/verify?token=abc"


def test_other_senders_are_ignored():
    m = [msg("Bank <a@bank.example>", "Code", "Your code is 123456")]
    assert find_code(m, ["greenhouse.io"], SINCE) is None


def test_subdomain_sender_allowed():
    m = [msg("no-reply@us.greenhouse-mail.io", "Code", "Your code is 123456")]
    assert find_code(m, ["greenhouse-mail.io"], SINCE) == "123456"


def test_lookalike_domain_rejected():
    m = [msg("a@evilgreenhouse.io", "Code", "Your code is 123456")]
    assert find_code(m, ["greenhouse.io"], SINCE) is None


def test_old_mail_ignored():
    m = [msg("a@greenhouse.io", "Code", "Your code is 123456",
             NOW - timedelta(hours=2))]
    assert find_code(m, ["greenhouse.io"], SINCE) is None


def test_newest_first():
    m = [msg("a@greenhouse.io", "x", "code 111111", NOW - timedelta(minutes=5)),
         msg("a@greenhouse.io", "x", "code 222222", NOW)]
    assert find_code(m, ["greenhouse.io"], SINCE) == "222222"


def test_missing_credentials_returns_none(monkeypatch):
    monkeypatch.setattr(mail_codes.config, "IMAP_PASSWORD", None)
    assert mail_codes.get_code(["greenhouse.io"]) == "none"


def test_server_exposes_exactly_one_tool():
    pytest.importorskip("mcp")
    server = mail_codes.build_server(["greenhouse.io"])
    tools = asyncio.run(server.list_tools())
    assert [t.name for t in tools] == ["get_verification_code"]
