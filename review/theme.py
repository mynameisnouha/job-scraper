"""
The "Organic" look for the Streamlit app: one palette, two typefaces, pills
everywhere, and the HTML fragments the pages are built from.

Streamlit draws its own widgets; the CSS here restyles them to the design
tokens. The builder functions return HTML strings for the parts Streamlit has
no widget for — the score disc, the gate chips, the stat tiles, the timeline —
and stay free of Streamlit so they can be tested as plain functions.

Every builder escapes the text it is given. Job titles and verdicts come from
scraped pages and an LLM, so nothing goes into the page unescaped.
"""
from html import escape
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ── tokens ──────────────────────────────────────────────────────────────────
BG = "#f7f6fd"
SURFACE = "#eceafa"
TEXT = "#26243a"
ACCENT = "#7468cd"
ACCENT_2 = "#3691c3"

NEUTRAL = {100: "#f9f9fd", 200: "#eeedf6", 300: "#dcdaea", 400: "#bfbcd3", 500: "#a09db6",
           600: "#7f7c97", 700: "#605d78", 800: "#444158", 900: "#2b2940"}
PURPLE = {100: "#f5f3ff", 200: "#e7e3fd", 300: "#d1cbf8", 400: "#b2a9ef", 500: "#9288e2",
          600: "#7468cd", 700: "#5a4ea9", 800: "#42387e", 900: "#2c2654"}
BLUE = {100: "#eef8fd", 200: "#d7eefa", 300: "#b4e0f5", 400: "#85c9ec", 500: "#57aedc",
        600: "#3691c3", 700: "#26709b", 800: "#1a5172", 900: "#10344a"}

FONT_HEADING = "'Caprasimo', Georgia, serif"
FONT_BODY = "'Figtree', system-ui, sans-serif"
FONT_MONO = "'Source Code Pro', ui-monospace, monospace"


def esc(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


# ── score bands ─────────────────────────────────────────────────────────────
# Bands sit against the observed distribution of the scorer, not an absolute
# 0-100 scale: almost nothing scores above 82 and the mean sits in the 40s, so
# a 65 is a strong posting and a 50 is still worth the hour.
def band(score: Optional[int]) -> Dict[str, str]:
    if score is None:
        return {"band": "Unscored", "score_bg": NEUTRAL[200], "score_fg": NEUTRAL[600],
                "band_fg": NEUTRAL[600]}
    if score >= 65:
        return {"band": "Strong", "score_bg": ACCENT, "score_fg": BG, "band_fg": PURPLE[800]}
    if score >= 50:
        return {"band": "Worth it", "score_bg": PURPLE[300], "score_fg": PURPLE[900],
                "band_fg": PURPLE[800]}
    if score >= 38:
        return {"band": "Marginal", "score_bg": NEUTRAL[300], "score_fg": NEUTRAL[900],
                "band_fg": NEUTRAL[700]}
    return {"band": "Long shot", "score_bg": NEUTRAL[200], "score_fg": NEUTRAL[700],
            "band_fg": NEUTRAL[600]}


def corpus_rank(score: Optional[int], all_scores: Sequence[int]) -> str:
    """'top 4% of 1302' — where this score sits among every scored posting."""
    if score is None or not all_scores:
        return ""
    above = sum(1 for s in all_scores if s > score)
    share = max(1, round(100 * (above + 1) / len(all_scores)))
    return f"top {share}% of {len(all_scores)}"


# ── the German gate ─────────────────────────────────────────────────────────
# The four states the scorer actually distinguishes, and the one that matters
# most: an ad written in German that names no level. That is a question for
# the recruiter, not a settled fact either way, and it gets an outlined chip so
# it never reads as "no German".
_GATES = {
    "none": ("No German", BLUE[300], BLUE[900], "transparent"),
    "nice-to-have": ("German nice-to-have", BLUE[200], BLUE[900], "transparent"),
    "B1": ("B1", NEUTRAL[200], NEUTRAL[900], "transparent"),
    "B2": ("B2", NEUTRAL[300], NEUTRAL[900], "transparent"),
    "C1-fluent": ("C1 — hard gate", PURPLE[300], PURPLE[900], "transparent"),
}


def german_gate(breakdown: Dict[str, Any]) -> Dict[str, str]:
    b = breakdown or {}
    level = str(b.get("german_required") or "").strip()
    if level in _GATES:
        label, bg, fg, border = _GATES[level]
    elif b.get("jd_language") in ("de", "mixed"):
        label, bg, fg, border = "Unstated · ad in German", "transparent", NEUTRAL[800], NEUTRAL[400]
    else:
        label, bg, fg, border = "German not stated", "transparent", NEUTRAL[800], NEUTRAL[400]
    return {"label": label, "bg": bg, "fg": fg, "border": border}


# ── small pieces ────────────────────────────────────────────────────────────
_GLOBE = ('<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
          'stroke-width="2.75" stroke-linecap="round" stroke-linejoin="round" style="flex:none">'
          '<circle cx="12" cy="12" r="9"></circle><path d="M3.6 9h16.8M3.6 15h16.8"></path>'
          '<path d="M12 3a15 15 0 0 0 0 18a15 15 0 0 0 0-18"></path></svg>')
_CLOCK = ('<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
          'stroke-width="2.75" stroke-linecap="round" style="flex:none;opacity:.6">'
          '<circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 2"></path></svg>')
_TREND = ('<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
          'stroke-width="2.75" stroke-linecap="round" stroke-linejoin="round" '
          'style="flex:none;opacity:.6"><path d="M4 18 10 9l4 5 6-9"></path></svg>')


def kicker(text: str, color: str = NEUTRAL[600]) -> str:
    return (f'<div style="font-size:10px;letter-spacing:.14em;text-transform:uppercase;'
            f'color:{color};margin-bottom:8px">{esc(text)}</div>')


def chip(text: str, bg: str = BG, fg: str = NEUTRAL[900], icon: str = "",
         border: str = "transparent", bold: bool = False) -> str:
    weight = "font-weight:700;letter-spacing:.04em;text-transform:uppercase;font-size:12px;" \
        if bold else "font-size:12.5px;"
    return (f'<span style="display:inline-flex;align-items:center;gap:7px;{weight}'
            f'padding:6px 13px;border-radius:999px;white-space:nowrap;background:{bg};'
            f'color:{fg};border:1px solid {border}">{icon}{esc(text)}</span>')


def gate_chip(breakdown: Dict[str, Any]) -> str:
    g = german_gate(breakdown)
    return chip(g["label"], g["bg"], g["fg"], icon=_GLOBE, border=g["border"], bold=True)


def fact_chips(breakdown: Dict[str, Any], facts: Iterable[Tuple[str, str]]) -> str:
    """The gate row: German always first, then effort, odds and the rest."""
    parts = [gate_chip(breakdown)]
    for label, value in facts:
        if label == "German":
            continue  # already the first chip
        if label == "Effort":
            parts.append(chip(value, icon=_CLOCK))
        elif label == "Interview odds":
            parts.append(chip(f"{value} interview odds", icon=_TREND))
        elif label == "Source":
            parts.append(chip(value, PURPLE[200], PURPLE[800]))
        else:
            parts.append(chip(f"{label} · {value}"))
    return ('<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">'
            + "".join(parts) + "</div>")


def kbd(key: str, dark: bool = False) -> str:
    bg, fg = (PURPLE[700], PURPLE[100]) if dark else (NEUTRAL[300], NEUTRAL[800])
    return (f'<span style="font-family:{FONT_MONO};font-size:11px;padding:1px 6px;'
            f'border-radius:5px;background:{bg};color:{fg}">{esc(key)}</span>')


def program_badge() -> str:
    return (f'<span style="font-size:11px;letter-spacing:.06em;text-transform:uppercase;'
            f'font-weight:700;padding:3px 10px;border-radius:999px;background:{NEUTRAL[800]};'
            f'color:{NEUTRAL[100]}">Programme</span>')


# ── composites ──────────────────────────────────────────────────────────────
def score_disc(score: Optional[int], all_scores: Sequence[int] = (), size: int = 68) -> str:
    """The verdict as a disc: number, band word, place in the corpus."""
    b = band(score)
    number = "—" if score is None else str(score)
    font = 25 if size < 80 else 36
    rank = corpus_rank(score, all_scores)
    rank_html = (f'<span style="font-size:11px;color:{NEUTRAL[600]};text-align:center;'
                 f'line-height:1.3;max-width:{size + 24}px">{esc(rank)}</span>') if rank else ""
    return (
        f'<div style="display:flex;flex-direction:column;align-items:center;gap:7px;padding-top:3px">'
        f'<div style="width:{size}px;height:{size}px;border-radius:999px;display:flex;'
        f'align-items:center;justify-content:center;background:{b["score_bg"]};color:{b["score_fg"]}">'
        f'<span style="font-family:{FONT_HEADING};font-size:{font}px;line-height:1">{number}</span></div>'
        f'<span style="font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;font-weight:700;'
        f'white-space:nowrap;color:{b["band_fg"]}">{esc(b["band"])}</span>{rank_html}</div>'
    )


def title_block(title: str, company: str, found: Optional[str], url: Optional[str] = None,
                is_program: bool = False, size: int = 22) -> str:
    """Title, then company · found · link, on one line that wraps."""
    meta = [f'<span style="font-weight:600">{esc(company)}</span>']
    if found:
        meta.append(f'<span style="color:{NEUTRAL[500]}">·</span><span>{esc(found)}</span>')
    if url:
        meta.append(f'<span style="color:{NEUTRAL[500]}">·</span>'
                    f'<a href="{esc(url)}" target="_blank" rel="noopener">Open posting ↗</a>')
    if is_program:
        meta.append(program_badge())
    return (
        f'<h3 style="margin:0 0 3px;font-family:{FONT_HEADING};font-weight:400;font-size:{size}px;'
        f'line-height:1.2;color:{TEXT}">{esc(title)}</h3>'
        f'<div style="font-size:14.5px;color:{NEUTRAL[800]};display:flex;align-items:center;'
        f'gap:9px;flex-wrap:wrap">{"".join(meta)}</div>'
    )


def verdict(text: str, size: float = 15.5) -> str:
    if not text:
        return ""
    return (f'<p style="margin:0;font-size:{size}px;line-height:1.55;color:{NEUTRAL[900]};'
            f'max-width:56em">{esc(text)}</p>')


def tinted_list(heading: str, items: Sequence[str], tone: str = "blue") -> str:
    """'Lead with' (blue) and 'They'll push back on' (purple) panels."""
    ramp = BLUE if tone == "blue" else PURPLE
    rows = "".join(f'<div style="font-size:14.5px;line-height:1.5;color:{ramp[900]}">{esc(i)}</div>'
                   for i in items) or \
        f'<div style="font-size:13.5px;color:{ramp[800]};opacity:.8">Nothing recorded.</div>'
    return (f'<div style="background:{ramp[200]};border-radius:16px;padding:20px 22px">'
            f'<div style="font-family:{FONT_HEADING};font-size:15px;color:{ramp[900]};'
            f'margin-bottom:11px">{esc(heading)}</div>'
            f'<div style="display:flex;flex-direction:column;gap:10px">{rows}</div></div>')


def two_up(left: str, right: str) -> str:
    return (f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));'
            f'gap:16px">{left}{right}</div>')


def fix_list(items: Sequence[str], effort: Optional[str] = None) -> str:
    """'Fix before applying' — each item is a task with a checkbox glyph."""
    if not items:
        return ""
    meta = f" · {len(items)} item{'s' if len(items) != 1 else ''}"
    if effort:
        meta += f", {effort}"
    rows = "".join(
        f'<div style="display:flex;gap:12px;align-items:flex-start;font-size:14.5px;line-height:1.5">'
        f'<span style="flex:none;width:18px;height:18px;border-radius:6px;border:1.5px solid '
        f'{NEUTRAL[400]};margin-top:2px"></span><span>{esc(i)}</span></div>' for i in items)
    return (f'<div style="background:{BG};border-radius:16px;padding:20px 22px">'
            f'<div style="font-family:{FONT_HEADING};font-size:15px;margin-bottom:11px">'
            f'Fix before applying{esc(meta)}</div>'
            f'<div style="display:flex;flex-direction:column;gap:12px">{rows}</div></div>')


def gate_row(breakdown: Dict[str, Any], facts: Iterable[Tuple[str, str]]) -> str:
    """The focus card's gate line: German chip, then label/value pills."""
    parts = [gate_chip(breakdown)]
    for label, value in facts:
        if label == "German":
            continue
        parts.append(
            f'<span style="display:inline-flex;align-items:baseline;gap:8px;font-size:13.5px;'
            f'padding:8px 16px;border-radius:999px;white-space:nowrap;background:{BG}">'
            f'<span style="font-size:10px;letter-spacing:.1em;text-transform:uppercase;'
            f'color:{NEUTRAL[600]}">{esc(label)}</span><b>{esc(value)}</b></span>')
    return (kicker("Gates") + '<div style="display:flex;gap:8px;flex-wrap:wrap">'
            + "".join(parts) + "</div>")


def context_list(pairs: Sequence[Tuple[str, str]], note: str = "") -> str:
    rows = "".join(
        f'<div><div style="font-size:11px;letter-spacing:.06em;text-transform:uppercase;'
        f'color:{NEUTRAL[600]};margin-bottom:2px">{esc(label)}</div>'
        f'<div style="font-size:14px;line-height:1.45">{esc(value)}</div></div>'
        for label, value in pairs)
    if not rows:
        rows = f'<div style="font-size:13.5px;color:{NEUTRAL[600]}">The scorer recorded no context.</div>'
    foot = (f'<div style="margin-top:16px;padding-top:14px;border-top:1px solid rgba(38,36,58,.14);'
            f'font-size:12.5px;line-height:1.5;color:{NEUTRAL[700]}">{esc(note)}</div>') if note else ""
    return (kicker("Context") + f'<div style="display:flex;flex-direction:column;gap:11px">{rows}</div>'
            + foot)


def key_legend(shortcuts: Sequence[Tuple[str, str]]) -> str:
    items = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:6px;font-size:12px;'
        f'padding:5px 10px 5px 6px;border-radius:999px;background:{BG}">'
        f'<b style="font-family:{FONT_MONO};font-size:11px;background:{NEUTRAL[300]};'
        f'color:{NEUTRAL[900]};border-radius:5px;padding:2px 6px">{esc(key)}</b>'
        f'{esc(label.lower())}</span>' for key, label in shortcuts)
    return kicker("Keys") + f'<div style="display:flex;flex-wrap:wrap;gap:7px">{items}</div>'


def stat_tiles(tiles: Sequence[Tuple[str, str, str]]) -> str:
    """(label, value, note) → a row of surface tiles."""
    cells = "".join(
        f'<div style="background:{SURFACE};border-radius:16px;padding:20px 22px">'
        f'<div style="font-size:11px;letter-spacing:.1em;text-transform:uppercase;'
        f'color:{NEUTRAL[600]};margin-bottom:7px">{esc(label)}</div>'
        f'<div style="font-family:{FONT_HEADING};font-size:34px;line-height:1">{esc(value)}</div>'
        f'<div style="font-size:12.5px;color:{NEUTRAL[700]};margin-top:6px;line-height:1.4">'
        f'{esc(note)}</div></div>' for label, value, note in tiles)
    return (f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));'
            f'gap:14px">{cells}</div>')


def banner(headline: str, body: str, tone: str = "purple", big: str = "", big_note: str = "") -> str:
    """The lead-with-the-answer block at the top of a page."""
    ramp = PURPLE if tone == "purple" else BLUE
    lead = ""
    if big:
        lead = (f'<div style="flex:none"><div style="font-family:{FONT_HEADING};font-size:58px;'
                f'line-height:1;color:{ramp[800]}">{esc(big)}</div>'
                f'<div style="font-size:12px;letter-spacing:.1em;text-transform:uppercase;'
                f'color:{ramp[800]};margin-top:8px">{esc(big_note)}</div></div>')
    return (f'<div style="background:{ramp[200]};border-radius:32px;padding:30px 36px;display:flex;'
            f'gap:34px;align-items:center;flex-wrap:wrap">{lead}<div style="flex:1;min-width:260px">'
            f'<div style="font-family:{FONT_HEADING};font-size:22px;line-height:1.25;color:{ramp[900]};'
            f'margin-bottom:9px">{esc(headline)}</div><p style="margin:0;font-size:15px;line-height:1.6;'
            f'color:{ramp[900]};max-width:46em">{esc(body)}</p></div></div>')


def page_header(title: str, sub_html: str) -> str:
    return (f'<h1 style="margin:0 0 7px;font-family:{FONT_HEADING};font-weight:400;font-size:44px;'
            f'line-height:1.05;color:{TEXT}">{esc(title)}</h1>'
            f'<p style="margin:0 0 6px;font-size:16px;color:{NEUTRAL[800]};line-height:1.5">{sub_html}</p>')


def bar_rows(rows: Sequence[Dict[str, Any]], label_width: int = 128) -> str:
    """Horizontal bars: label · bar · count. Each row: label, share (0-1), fill, n."""
    out = []
    for r in rows:
        width = max(2, round(100 * float(r.get("share") or 0)))
        out.append(
            f'<div style="display:flex;align-items:center;gap:14px">'
            f'<div style="width:{label_width}px;flex:none;font-size:13.5px;text-align:right;'
            f'line-height:1.3">{esc(r["label"])}</div>'
            f'<div style="flex:1;height:26px;border-radius:999px;background:{BG};overflow:hidden;display:flex">'
            f'<div style="height:100%;background:{r.get("fill", PURPLE[500])};width:{width}%"></div></div>'
            f'<div style="width:34px;flex:none;font-family:{FONT_HEADING};font-size:15px">{esc(r["n"])}</div>'
            f'</div>')
    return '<div style="display:flex;flex-direction:column;gap:13px">' + "".join(out) + "</div>"


def bucket_bars(rows: Sequence[Dict[str, Any]]) -> str:
    """Interview rate per band: label + n on the left, rate on the right, bar below."""
    out = []
    best = max((r.get("rate") or 0) for r in rows) if rows else 0
    for r in rows:
        rate = r.get("rate")
        width = 0 if rate is None else max(2, round(100 * rate))
        fill = ACCENT if rate is not None and rate == best and best > 0 else PURPLE[400]
        shown = "—" if rate is None else f"{rate * 100:.0f}%"
        out.append(
            f'<div><div style="display:flex;justify-content:space-between;align-items:baseline;'
            f'font-size:13.5px;margin-bottom:6px"><span><b>{esc(r["label"])}</b> '
            f'<span style="color:{NEUTRAL[600]}">· {esc(r["n"])} resolved</span></span>'
            f'<b style="font-family:{FONT_HEADING};font-size:16px">{shown}</b></div>'
            f'<div style="height:12px;border-radius:999px;background:{BG};overflow:hidden">'
            f'<div style="height:100%;border-radius:999px;background:{fill};width:{width}%"></div></div></div>')
    return '<div style="display:flex;flex-direction:column;gap:16px">' + "".join(out) + "</div>"


def legend(pairs: Sequence[Tuple[str, str]]) -> str:
    items = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:7px"><span style="width:11px;'
        f'height:11px;border-radius:999px;background:{color}"></span>{esc(label)}</span>'
        for color, label in pairs)
    return (f'<div style="display:flex;gap:18px;margin-top:20px;padding-top:18px;border-top:1px solid '
            f'rgba(38,36,58,.14);font-size:12.5px;color:{NEUTRAL[700]};flex-wrap:wrap">{items}</div>')


_DOT = {
    "done": (BLUE[600], BLUE[600], TEXT),
    "current": (ACCENT, ACCENT, PURPLE[800]),
    "bad": (PURPLE[600], PURPLE[600], PURPLE[800]),
    "pending": ("transparent", NEUTRAL[400], NEUTRAL[600]),
}


def timeline_html(nodes: Sequence[Dict[str, str]]) -> str:
    out = []
    for i, node in enumerate(nodes):
        dot_bg, dot_border, fg = _DOT.get(node["state"], _DOT["pending"])
        line = ""
        if i < len(nodes) - 1:
            line_bg = BLUE[500] if node["state"] == "done" else NEUTRAL[300]
            line = f'<span style="width:42px;height:2px;background:{line_bg};margin-bottom:26px"></span>'
        out.append(
            f'<div style="display:flex;align-items:center;flex:none">'
            f'<div style="display:flex;flex-direction:column;align-items:center;gap:6px;min-width:96px">'
            f'<span style="width:15px;height:15px;border-radius:999px;background:{dot_bg};'
            f'border:2px solid {dot_border}"></span>'
            f'<span style="font-size:12px;font-weight:600;color:{fg};text-align:center;line-height:1.2">'
            f'{esc(node["label"])}</span>'
            f'<span style="font-size:11px;color:{NEUTRAL[600]}">{esc(node.get("date") or "")}</span>'
            f'</div>{line}</div>')
    return ('<div style="display:flex;align-items:center;flex-wrap:nowrap;overflow-x:auto;'
            'padding-bottom:4px">' + "".join(out) + "</div>")


def stage_pill(label: str, tone: str) -> str:
    bg, fg = {
        "good": (BLUE[300], BLUE[900]),
        "bad": (PURPLE[300], PURPLE[900]),
        "wait": (NEUTRAL[300], NEUTRAL[900]),
    }.get(tone, (NEUTRAL[200], NEUTRAL[800]))
    return (f'<span style="display:inline-flex;align-items:center;gap:8px;font-family:{FONT_HEADING};'
            f'font-size:14px;padding:9px 18px;border-radius:999px;white-space:nowrap;background:{bg};'
            f'color:{fg}"><span style="width:8px;height:8px;border-radius:999px;background:currentColor;'
            f'opacity:.8"></span>{esc(label)}</span>')


def score_pill(score: Optional[int]) -> str:
    b = band(score)
    number = "—" if score is None else str(score)
    return (f'<span style="font-size:12px;font-weight:700;padding:4px 11px;border-radius:999px;'
            f'white-space:nowrap;background:{b["score_bg"]};color:{b["score_fg"]}">'
            f'{number} · {esc(b["band"])}</span>')


def labelled_box(label: str, text: str, label_color: str) -> str:
    return (f'<div style="background:{BG};border-radius:16px;padding:16px 18px">'
            f'<div style="font-size:10px;letter-spacing:.12em;text-transform:uppercase;'
            f'color:{label_color};margin-bottom:7px">{esc(label)}</div>'
            f'<div style="font-size:14px;line-height:1.55">{esc(text) or "—"}</div></div>')


def step_bar(steps: Sequence[Dict[str, str]]) -> str:
    """The tailor page's three steps, each with a state: done / current / todo."""
    cells = []
    for s in steps:
        state = s.get("state", "todo")
        if state == "done":
            bg, fg, sub, dot_bg, dot_fg, n = BLUE[300], BLUE[900], BLUE[800], BLUE[700], BLUE[100], "✓"
        elif state == "current":
            bg, fg, sub, dot_bg, dot_fg, n = ACCENT, BG, PURPLE[100], PURPLE[700], PURPLE[100], s["n"]
        else:
            bg, fg, sub, dot_bg, dot_fg, n = "transparent", TEXT, NEUTRAL[600], NEUTRAL[300], NEUTRAL[800], s["n"]
        cells.append(
            f'<div style="flex:1;min-width:180px;display:flex;align-items:center;gap:12px;padding:12px 20px;'
            f'border-radius:999px;background:{bg}"><span style="flex:none;width:28px;height:28px;'
            f'border-radius:999px;display:flex;align-items:center;justify-content:center;'
            f'font-family:{FONT_HEADING};font-size:14px;background:{dot_bg};color:{dot_fg}">{esc(n)}</span>'
            f'<div style="min-width:0"><div style="font-family:{FONT_HEADING};font-size:15px;color:{fg};'
            f'line-height:1.2">{esc(s["label"])}</div><div style="font-size:12.5px;color:{sub};'
            f'line-height:1.3">{esc(s.get("note", ""))}</div></div></div>')
    return (f'<div style="display:flex;align-items:stretch;background:{SURFACE};border-radius:999px;'
            f'padding:6px;flex-wrap:wrap">{"".join(cells)}</div>')


def coverage_bar(now: float, if_written: float) -> str:
    """Purple is what the CV proves today; blue is what it would after an edit."""
    now_w = max(0, min(100, round(100 * now)))
    gain_w = max(0, min(100 - now_w, round(100 * (if_written - now))))
    return (f'<div style="height:16px;border-radius:999px;background:{BG};overflow:hidden;display:flex">'
            f'<div style="height:100%;background:{PURPLE[500]};width:{now_w}%"></div>'
            f'<div style="height:100%;background:{BLUE[400]};width:{gain_w}%"></div></div>')


def mono_chips(items: Iterable[str]) -> str:
    return ('<div style="display:flex;flex-wrap:wrap;gap:7px">' + "".join(
        f'<span style="font-family:{FONT_MONO};font-size:12px;padding:5px 11px;border-radius:999px;'
        f'background:{BG}">{esc(i)}</span>' for i in items) + "</div>")


def pulse_card(pulse: Optional[Dict[str, Any]], when: str) -> str:
    """The sidebar's 'Last scrape' box."""
    if not pulse:
        body = f'<div style="font-size:12.5px;color:{NEUTRAL[700]}">Not reachable right now.</div>'
    else:
        sources = pulse.get("sources") or []
        status = (f"{len(sources)} source{'s' if len(sources) != 1 else ''} reporting"
                  if sources else "No source reported in the last day")
        body = (f'<div style="font-family:{FONT_HEADING};font-size:17px">{esc(when)}</div>'
                f'<div style="font-size:12.5px;line-height:1.5;color:{NEUTRAL[700]}">'
                f'{pulse["new"]} new postings · {pulse["cleared"]} cleared the bar</div>'
                f'<div style="display:flex;align-items:center;gap:7px;margin-top:2px">'
                f'<span style="width:7px;height:7px;border-radius:999px;background:{BLUE[600]};flex:none"></span>'
                f'<span style="font-size:12.5px;color:{NEUTRAL[700]}">{esc(status)}</span></div>')
    return (f'<div style="background:{BG};border-radius:16px;padding:15px 16px;display:flex;'
            f'flex-direction:column;gap:8px">{kicker("Last scrape")}{body}</div>')


def brand() -> str:
    return (
        '<div style="display:flex;align-items:center;gap:11px;padding:4px 6px 14px">'
        f'<span style="width:34px;height:34px;border-radius:999px;background:{ACCENT};display:flex;'
        'align-items:center;justify-content:center;flex:none"><svg width="18" height="18" viewBox="0 0 24 24" '
        'fill="none" stroke="#f5ead8" stroke-width="2.75" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 20h16"></path><path d="M7 20V9"></path><path d="M12 20V4"></path><path d="M17 20v-7"></path>'
        f'</svg></span><span style="font-family:{FONT_HEADING};font-size:19px;line-height:1;color:{TEXT}">'
        'Job Hunt</span></div>')


# ── the stylesheet ──────────────────────────────────────────────────────────
CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Caprasimo&family=Figtree:wght@400;600;700&family=Source+Code+Pro:wght@400;600&display=swap');

:root {{
  --jh-bg: {BG}; --jh-surface: {SURFACE}; --jh-text: {TEXT}; --jh-accent: {ACCENT};
  --jh-divider: rgba(38,36,58,.14);
}}
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {{
  font-family: {FONT_BODY};
  color: var(--jh-text);
}}
.stApp {{ background: var(--jh-bg); }}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stMainBlockContainer"] {{ padding: 2.4rem 2.8rem 6rem; max-width: 1360px; }}
h1, h2, h3, h4, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {{
  font-family: {FONT_HEADING}; font-weight: 400; letter-spacing: -0.015em;
}}
a {{ color: {PURPLE[700]}; text-decoration: none; }}
a:hover {{ color: {PURPLE[800]}; text-decoration: underline; }}
::selection {{ background: rgba(116,104,205,.3); }}
::-webkit-scrollbar {{ width: 10px; height: 10px; }}
::-webkit-scrollbar-thumb {{ background: {NEUTRAL[400]}; border-radius: 999px; }}

/* — sidebar: brand, grouped nav as pills, last-scrape card — */
[data-testid="stSidebar"] {{ background: var(--jh-surface); }}
[data-testid="stSidebar"] [data-testid="stSidebarContent"] {{ padding-top: 1.4rem; }}
[data-testid="stSidebar"] .stButton > button {{
  width: 100%; padding: 9px 12px; border: 0; border-radius: 999px; min-height: 0;
  font-family: {FONT_BODY}; font-size: 15px;
}}
[data-testid="stSidebar"] .stButton > button > div {{ justify-content: flex-start; width: 100%; }}
[data-testid="stSidebar"] .stButton > button p {{
  display: flex; align-items: center; width: 100%; font-size: 15px; gap: 8px;
}}
[data-testid="stSidebar"] .stButton > button [data-testid="stMarkdownContainer"] {{ width: 100%; }}
[data-testid="stSidebar"] .stButton > button > div > span {{ width: 100%; }}
[data-testid="stSidebar"] .stButton > button code {{
  margin-left: auto; font-family: {FONT_BODY}; font-size: 12px; font-weight: 700; min-width: 22px;
  text-align: center; padding: 2px 7px; border-radius: 999px; background: {NEUTRAL[300]};
  color: {NEUTRAL[800]};
}}
[data-testid="stSidebar"] .stButton > button[data-testid="stBaseButton-primary"] code {{
  background: {PURPLE[700]}; color: {PURPLE[100]};
}}
[data-testid="stSidebar"] .stButton > button[data-testid="stBaseButton-secondary"] {{ background: transparent; color: var(--jh-text); }}
[data-testid="stSidebar"] .stButton > button[data-testid="stBaseButton-secondary"]:hover {{ background: {NEUTRAL[200]}; }}
[data-testid="stSidebar"] .stButton > button[data-testid="stBaseButton-primary"] {{ font-family: {FONT_BODY}; }}
.jh-navlabel {{
  font-size: 10px; letter-spacing: .14em; text-transform: uppercase; color: {NEUTRAL[600]};
  padding: 14px 10px 4px;
}}

/* — buttons: pills, heading face on the primary — */
.stButton > button, .stLinkButton > a, .stDownloadButton > button, [data-testid="stPopoverButton"],
.stFormSubmitButton > button {{
  border-radius: 999px; font-family: {FONT_BODY}; font-size: 14px; border: 1px solid var(--jh-divider);
  background: transparent; color: var(--jh-text); min-height: 40px; padding: 0 18px; white-space: nowrap;
}}
.stButton > button:hover, .stLinkButton > a:hover, [data-testid="stPopoverButton"]:hover,
.stDownloadButton > button:hover {{
  background: rgba(38,36,58,.07); border-color: var(--jh-divider); color: var(--jh-text);
}}
.stButton > button[data-testid="stBaseButton-primary"], .stFormSubmitButton > button[kind="primary"],
.stDownloadButton > button[data-testid="stBaseButton-primary"] {{
  background: var(--jh-accent); color: var(--jh-bg); border: 0; font-family: {FONT_HEADING};
}}
.stButton > button[data-testid="stBaseButton-primary"] p {{ color: var(--jh-bg); }}
.stButton > button[data-testid="stBaseButton-primary"]:hover {{ background: {PURPLE[600]}; color: var(--jh-bg); }}
.stButton > button[data-testid="stBaseButton-primary"]:active {{ background: {PURPLE[700]}; }}
.stButton > button[data-testid="stBaseButton-tertiary"] {{ border: 0; color: {PURPLE[700]}; padding: 0 6px; min-height: 0; }}
.stButton > button[data-testid="stBaseButton-tertiary"]:hover {{ background: rgba(116,104,205,.1); }}
.stButton > button code, .stLinkButton > a code {{
  font-family: {FONT_MONO}; font-size: 11px; padding: 1px 6px; border-radius: 5px;
  background: {NEUTRAL[300]}; color: {NEUTRAL[800]}; margin-left: 4px;
}}
.stButton > button[data-testid="stBaseButton-primary"] code {{ background: {PURPLE[700]}; color: {PURPLE[100]}; }}
.stButton > button:disabled {{ opacity: .45; }}

/* — radio as a segmented pill — */
[data-testid="stRadio"] [role="radiogroup"] {{
  display: inline-flex; flex-wrap: wrap; gap: 0; background: var(--jh-surface);
  border-radius: 999px; padding: 3px;
}}
[data-testid="stRadioOption"] {{
  padding: 7px 16px !important; border-radius: 999px; margin: 0 !important; cursor: pointer;
  font-size: 13.5px; transition: background .12s;
}}
[data-testid="stRadioOption"] > div > div > div:first-child {{ display: none; }}
[data-testid="stRadioOption"] p {{ font-size: 13.5px; }}
[data-testid="stRadioOption"][data-selected="true"] {{ background: var(--jh-accent); }}
[data-testid="stRadioOption"][data-selected="true"] p {{ color: var(--jh-bg) !important; }}
[data-testid="stRadioOption"]:not([data-selected="true"]):hover {{ background: rgba(38,36,58,.07); }}
[data-testid="stRadio"] [data-testid="stWidgetLabel"] {{ display: none; }}

/* — inputs — */
[data-testid="stTextInputRootElement"], [data-testid="stTextInputRootElement"]:focus-within {{
  background: var(--jh-surface); border: 0; border-radius: 999px; box-shadow: none;
}}
[data-testid="stTextInputRootElement"] input {{
  background: transparent; font-family: {FONT_BODY}; font-size: 14px; color: var(--jh-text);
  padding-left: 18px; min-height: 40px;
}}
[data-testid="stTextArea"] textarea, [data-testid="stTextArea"] > div > div {{
  background: var(--jh-surface); border: 0; border-radius: 16px; font-family: {FONT_BODY};
}}
[data-testid="stSelectbox"] > div > div, [data-testid="stNumberInput"] > div > div {{
  background: var(--jh-surface); border: 0; border-radius: 999px; font-family: {FONT_BODY};
}}

/* — surface cards: bordered containers, and lighter when nested — */
[data-testid="stVerticalBlockBorderWrapper"] {{
  background: var(--jh-surface); border: 0 !important; border-radius: 32px; padding: 22px 26px;
}}
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stVerticalBlockBorderWrapper"] {{
  background: var(--jh-bg); border-radius: 16px; padding: 16px 18px;
}}
.st-key-hidden-strip [data-testid="stVerticalBlockBorderWrapper"],
.st-key-hidden-strip {{ background: {BLUE[200]} !important; border-radius: 16px; padding: 10px 20px; }}
.st-key-hidden-strip .stButton > button {{
  background: var(--jh-bg); border: 0; min-height: 34px; padding: 0 12px 0 14px; font-size: 13px;
}}
.st-key-hidden-strip .stButton > button:hover {{ background: {BLUE[300]}; }}
.st-key-ghost-banner {{ background: {PURPLE[200]}; border-radius: 32px; padding: 18px 26px; }}
.st-key-ghost-banner .stButton > button {{ border-color: {PURPLE[600]}; color: {PURPLE[800]}; }}
.st-key-ghost-banner [data-testid="stBaseButton-primary"] {{ background: {PURPLE[700]}; color: {PURPLE[100]}; }}
.st-key-pitch-card {{ background: {BLUE[200]}; border-radius: 32px; padding: 20px 26px 12px; }}
.st-key-pitch-card [data-testid="stCode"] pre, .st-key-pitch-card pre, .st-key-pitch-card code {{
  background: transparent !important; font-family: {FONT_BODY} !important; font-size: 14.5px !important;
  line-height: 1.65 !important; color: {BLUE[900]} !important; white-space: pre-wrap !important;
}}
.st-key-pitch-card [data-testid="stCode"] {{ background: transparent; }}
.st-key-skip-panel {{ background: var(--jh-bg); border-radius: 16px; padding: 12px 18px 6px; }}
.st-key-skip-panel .stButton > button {{ background: var(--jh-surface); border-color: var(--jh-divider); min-height: 34px; font-size: 13px; }}

/* — misc widgets — */
[data-testid="stExpander"] {{ border: 0; border-radius: 16px; background: var(--jh-surface); }}
[data-testid="stExpander"] details {{ border: 0; }}
[data-testid="stExpander"] summary {{ font-family: {FONT_HEADING}; font-size: 15px; }}
[data-testid="stDialog"] > div, div[role="dialog"] {{ border-radius: 32px; }}
[data-testid="stCode"] pre {{ border-radius: 16px; background: var(--jh-bg); font-family: {FONT_MONO}; font-size: 12.5px; line-height: 1.8; }}
[data-testid="stTabs"] [role="tablist"] {{
  display: inline-flex; gap: 4px; background: var(--jh-bg); border-radius: 999px; padding: 4px;
  border: 0; box-shadow: none;
}}
[data-testid="stTab"] {{ border-radius: 999px; padding: 6px 16px; border: 0; }}
[data-testid="stTab"] p {{ font-size: 13.5px; }}
[data-testid="stTab"][data-selected="true"] {{ background: var(--jh-accent); }}
[data-testid="stTab"][data-selected="true"] p {{ color: var(--jh-bg) !important; }}
[data-testid="stTab"] > div:not([data-testid]) {{ display: none; }}
[data-testid="stTabPanel"] {{ padding-top: 14px; }}
[data-testid="stProgress"] > div > div {{ background: var(--jh-surface); border-radius: 999px; height: 6px; }}
[data-testid="stProgress"] > div > div > div {{ background: var(--jh-accent); border-radius: 999px; }}
[data-testid="stAlert"] {{ border-radius: 16px; }}
[data-testid="stMetric"] {{ background: var(--jh-surface); border-radius: 16px; padding: 18px 22px; }}
[data-testid="stMetricLabel"] {{ font-size: 11px; letter-spacing: .1em; text-transform: uppercase; color: {NEUTRAL[600]}; }}
[data-testid="stMetricValue"] {{ font-family: {FONT_HEADING}; font-size: 32px; }}
[data-testid="stDataFrame"], [data-testid="stTable"] {{ border-radius: 16px; overflow: hidden; }}
[data-testid="stCaptionContainer"] {{ color: {NEUTRAL[700]}; }}
[data-testid="stPopoverButton"] > div > div[aria-hidden="true"] {{ display: none; }}
[data-testid="stPopoverButton"] p {{ font-size: 15px; letter-spacing: .05em; }}
[data-testid="stPopoverBody"] {{ border-radius: 16px; }}
.st-key-skip-panel [data-testid="stHorizontalBlock"] {{ flex-wrap: wrap; }}
[data-testid="stPopoverBody"] .stButton > button, [data-testid="stPopoverBody"] .stLinkButton > a {{
  width: 100%; justify-content: flex-start; border: 0; min-height: 36px; padding: 0 12px; border-radius: 8px;
}}
[data-testid="stPopoverBody"] .stButton > button:hover {{ background: {NEUTRAL[200]}; }}
.jh-divider {{ height: 1px; background: var(--jh-divider); margin: 4px 0; }}
</style>
"""
