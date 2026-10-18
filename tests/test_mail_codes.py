import asyncio
from email.utils import format_datetime
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


G = ["greenhouse.io"]


def test_authenticated_dkim_aligned():
    assert mail_codes.authenticated(
        "mx.google.com; dkim=pass header.i=@mail.greenhouse.io header.s=x", G)


def test_authenticated_dkim_other_domain_only():
    assert not mail_codes.authenticated(
        "mx.google.com; dkim=pass header.d=sendgrid.net", G)


def test_authenticated_spf_pass_and_fail():
    assert mail_codes.authenticated(
        "mx; spf=pass smtp.mailfrom=bounce@greenhouse.io", G)
    assert not mail_codes.authenticated(
        "mx; spf=fail smtp.mailfrom=bounce@greenhouse.io", G)


def test_authenticated_missing_header():
    assert not mail_codes.authenticated("", G)


def test_tracking_host_link_not_returned():
    assert extract_code("s", "https://u123.ct.sendgrid.net/verify?x=1", G) is None


def test_unsubscribe_link_not_returned_and_code_wins():
    body = "https://greenhouse.io/unsubscribe?token=abc\nYour code is 654321"
    assert extract_code("s", body, G) == "654321"


def test_backslash_and_userinfo_host_not_returned():
    assert extract_code("s", "https://evil.com\\.greenhouse.io/verify", G) is None
    assert extract_code("s", "https://greenhouse.io@evil.com/verify", G) is None


def test_bad_word_after_verify_not_returned():
    assert extract_code("s", "https://greenhouse.io/verify/unsubscribe?token=x", G) is None


def test_authenticated_ignores_comments_and_lookalikes():
    assert not mail_codes.authenticated("mx; ( dkim=pass header.d=greenhouse.io )", G)
    assert not mail_codes.authenticated(
        "mx; dkim=pass header.d=greenhouse.io.evil.com", G)
    assert not mail_codes.authenticated(
        "mx; dkim=fail header.d=greenhouse.io; dkim=pass header.d=other.com", G)


def test_zipcode_not_a_code():
    assert extract_code("s", "zipcode 12345") is None


def test_display_name_spoof_rejected():
    m = [msg('"greenhouse.io" <x@evil.com>', "s", "Your code is 123456")]
    assert find_code(m, G, SINCE) is None


def test_uppercase_sender_accepted():
    m = [msg("A@GREENHOUSE.IO", "s", "Your code is 123456")]
    assert find_code(m, G, SINCE) == "123456"


def test_huge_since_minutes_returns_none(monkeypatch):
    monkeypatch.setattr(mail_codes, "fetch_messages", lambda s, d: [])
    assert mail_codes.get_code(G, 10**12) == "none"


class FakeIMAP:
    """Message 1 passes DKIM for greenhouse.io; message 2 fails it."""

    def __init__(self, host):
        pass

    def login(self, user, password):
        pass

    def select(self, box, readonly=False):
        assert readonly

    def search(self, *args):
        return "OK", [b"1 2"]

    def fetch(self, num, spec):
        good = num == b"1"
        auth = "mx.google.com; dkim=pass header.d=greenhouse.io" if good \
            else "mx.google.com; dkim=fail header.d=greenhouse.io"
        code = "111111" if good else "999999"
        # The unauthenticated mail is NEWER, so without the filter it would win.
        age = timedelta(minutes=5 if good else 1)
        date = format_datetime(datetime.now(timezone.utc) - age)
        raw = (f"From: Greenhouse <no-reply@greenhouse.io>\r\n"
               f"Subject: Hi\r\nAuthentication-Results: {auth}\r\n"
               f"Date: {date}\r\n\r\nYour code is {code}\r\n").encode()
        return "OK", [(b"1 (BODY[])", raw)]

    def logout(self):
        pass


def test_fake_imap_fetch_parse_and_auth_filter(monkeypatch):
    monkeypatch.setattr(mail_codes.imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setattr(mail_codes.config, "SMTP_USER", "me@x.test")
    monkeypatch.setattr(mail_codes.config, "IMAP_PASSWORD", "pw")
    assert mail_codes.get_code(G) == "111111"
