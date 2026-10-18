"""One-tool MCP server that hands the apply worker an email verification code.

The worker must never see the inbox: it can only ask for a code and gets back a
single string (a code, a verification link, or "none"). Subject and body text are
never returned, because mail is untrusted input and anything echoed to the worker
is a prompt-injection channel. Only mail from the ATS's own sender domains counts.

    python -m apply.mail_codes --sender-domains greenhouse.io,greenhouse-mail.io

Runs on the laptop only (needs `mcp`, see requirements-apply.txt). stdout is the
MCP stdio channel, so everything here logs to stderr.
"""

import argparse
import email
import email.policy
import email.utils
import html
import imaplib
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import config

logger = logging.getLogger(__name__)

NONE = "none"

_URL = re.compile(r"https://[^\s<>\"')\]]+", re.IGNORECASE)
_LINK_WORD = re.compile(r"verify|confirm|activate|token|code", re.IGNORECASE)
# A 4-8 digit code, or 6-8 alphanumerics containing a digit, shortly after a code
# word ("Your code is 123456", "Bestätigungscode: AB12CD").
_CODE = re.compile(
    r"(?:code|bestätigungscode|verification|otp|pin)\b"
    r"[^0-9A-Za-z]{0,10}(?:(?:is|ist|lautet)[^0-9A-Za-z]{0,10})?"
    r"(\d{4,8}|(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{6,8})(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def extract_code(subject: str, body: str) -> Optional[str]:
    """Return a verification link if present, else a code next to a code word."""
    text = f"{subject}\n{body}"
    for url in _URL.findall(text):
        url = url.rstrip(".,;")
        # Judge path/query only: the host itself may contain "code" or "token".
        rest = url.split("/", 3)[3] if url.count("/") >= 3 else ""
        if _LINK_WORD.search(rest):
            return url
    m = _CODE.search(text)
    return m.group(1) if m else None


def _sender_allowed(from_addr: str, sender_domains: list) -> bool:
    addr = email.utils.parseaddr(from_addr)[1]
    domain = addr.rpartition("@")[2].strip().lower()
    if not domain:
        return False
    for d in sender_domains:
        d = d.strip().lower().lstrip(".")
        if d and (domain == d or domain.endswith("." + d)):
            return True
    return False


def find_code(messages: Iterable, sender_domains: list,
              since: datetime) -> Optional[str]:
    """Newest allowed-sender mail since `since` that yields a code or link."""
    ok = [m for m in messages
          if m[3] >= since and _sender_allowed(m[0], sender_domains)]
    ok.sort(key=lambda m: m[3], reverse=True)
    for _from, subject, body, _date in ok:
        found = extract_code(subject, body)
        if found:
            return found
    return None


def _body_text(msg) -> str:
    plain = msg.get_body(preferencelist=("plain",))
    if plain is not None:
        return plain.get_content()
    page = msg.get_body(preferencelist=("html",))
    if page is not None:
        raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", page.get_content())
        return html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return ""


def fetch_messages(since: datetime) -> list:
    """Read INBOX read-only; return (from, subject, body, date) tuples.

    Returns [] (and logs) when credentials are missing or IMAP fails: the tool
    must answer "none", never raise into the worker.
    """
    user, password = config.SMTP_USER, config.IMAP_PASSWORD
    if not user or not password:
        logger.warning("mail_codes: SMTP_USER / IMAP_PASSWORD not set")
        return []
    out = []
    try:
        conn = imaplib.IMAP4_SSL(config.IMAP_HOST)
        try:
            conn.login(user, password)
            conn.select("INBOX", readonly=True)
            # SINCE is date-granular; the exact cut is applied in find_code.
            day = (since - timedelta(days=1)).strftime("%d-%b-%Y")
            _typ, data = conn.search(None, "SINCE", day)
            for num in (data[0] or b"").split():
                _typ, parts = conn.fetch(num, "(BODY.PEEK[])")
                if not parts or not isinstance(parts[0], tuple):
                    continue
                msg = email.message_from_bytes(parts[0][1],
                                               policy=email.policy.default)
                try:
                    date = email.utils.parsedate_to_datetime(msg["Date"])
                except (TypeError, ValueError):
                    continue
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                out.append((str(msg["From"] or ""), str(msg["Subject"] or ""),
                            _body_text(msg), date))
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception as e:  # noqa: BLE001 - never leak details to the worker
        logger.warning("mail_codes: IMAP failed: %s", type(e).__name__)
        return []
    return out


def get_code(sender_domains: list, since_minutes: int = 15) -> str:
    since = datetime.now(timezone.utc) - timedelta(minutes=max(1, since_minutes))
    return find_code(fetch_messages(since), sender_domains, since) or NONE


def build_server(sender_domains: list):
    """Build the MCP server. `mcp` is imported here, not at module top, so the
    pipeline (which does not install it) can import the pure functions."""
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp 2.x
    except ImportError:
        from mcp.server.fastmcp import FastMCP as Server  # mcp 1.x
    server = Server("mail_codes")

    @server.tool()
    def get_verification_code(since_minutes: int = 15) -> str:
        """Return the newest email verification code or link, or "none"."""
        return get_code(sender_domains, since_minutes)

    return server


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sender-domains", required=True,
                    help="comma-separated allowed sender domains")
    args = ap.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    domains = [d.strip() for d in args.sender_domains.split(",") if d.strip()]
    build_server(domains).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
