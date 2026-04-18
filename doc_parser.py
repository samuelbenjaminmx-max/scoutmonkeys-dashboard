"""
Google Doc HTML intake parser (export?format=html). Supports **tabbed** Docs: if the source URL
includes ``?tab=t.0`` (or other ``tab=`` values), the export request passes the same parameter so
Google returns that tab’s content instead of only the default tab.

Uses `cultural_daily_sponsored_rules.md` as the normative contract reference.
When `CRITICAL_RULES.md` exists in the repo, its full text is prepended to machine intake JSON for Claude
(pipeline-wide override for rules 1–11 — H1, body fidelity, client images, donation, category, focus keyword,
social image + exact 1920×1400, no AI liberties — see `CLAUDE.md`).
Implements an explicit decision tree for images, credits, and body structure.
**Http(s) anchors are inventoried only** (href, anchor text, bold, target, nofollow,
inline color) — there is no editorial-vs-paid taxonomy: on this site every body
link is a paid dofollow obligation (`CLAUDE.md`).

Run batch analysis:

    python doc_parser.py --batch data/training_docs.txt --out data/training_parse_report.json
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup, Tag

REPO_ROOT = Path(__file__).resolve().parent
RULES_FILE = REPO_ROOT / "cultural_daily_sponsored_rules.md"
CRITICAL_RULES_FILE = REPO_ROOT / "CRITICAL_RULES.md"


def extract_google_doc_id(url: str) -> str:
    """
    Document id from a full Google Docs URL, or return bare id if already passed.

    Supports:
    - …/document/d/DOC_ID/…
    - …/document/u/0/d/DOC_ID/… (and other /u/N/)
    """
    s = (url or "").strip()
    m = re.search(r"/document/d/([a-zA-Z0-9_-]+)", s)
    if m:
        return m.group(1)
    m = re.search(r"/document/u/\d+/d/([a-zA-Z0-9_-]+)", s)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]{10,}", s):
        return s
    raise ValueError(f"Could not parse Google Doc id from: {url!r}")


def extract_google_doc_tab_id(url: str) -> Optional[str]:
    """
    Tab id from a Google Docs **edit** URL query string, e.g. ``?tab=t.0`` or ``&tab=t.1``.

    Returns ``None`` when the parameter is absent. Values are restricted to a safe token shape
    (letters, digits, ``.``, ``_``, ``-``) as used by Google’s tab ids.
    """
    s = (url or "").strip()
    if not s:
        return None
    q = urllib.parse.urlparse(s).query
    if not q:
        return None
    tab = (urllib.parse.parse_qs(q, keep_blank_values=False).get("tab") or [None])[0]
    if not tab:
        return None
    tab = str(tab).strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", tab):
        return None
    return tab


def google_doc_export_url(doc_id: str, *, tab: Optional[str] = None) -> str:
    """Public HTML export URL for ``doc_id``, optionally scoped to a document tab."""
    u = f"https://docs.google.com/document/d/{doc_id}/export?format=html"
    if tab:
        u += "&" + urllib.parse.urlencode({"tab": tab})
    return u


def _normalize_gdoc_html(s: str) -> str:
    """NBSP / ZWSP normalization for regex scans (positions stay stable enough for heuristics)."""
    return (
        (s or "")
        .replace("\xa0", " ")
        .replace("\u200b", "")
        .replace("\ufeff", "")
    )


def fetch_google_doc_export_html(
    doc_id: str,
    *,
    tab: Optional[str] = None,
    attempts: int = 5,
) -> str:
    """Fetch `export?format=html` with short backoff on transient Google 5xx / 429."""
    import requests as rq

    export_url = google_doc_export_url(doc_id, tab=tab)
    headers = {"User-Agent": "ScoutmonkeysDocParser/1.0"}
    last: Any = None
    for i in range(attempts):
        r = rq.get(export_url, timeout=90, headers=headers)
        last = r
        if r.status_code in (500, 502, 503, 504, 429) and i + 1 < attempts:
            time.sleep(2.0 * (i + 1))
            continue
        r.raise_for_status()
        return r.text
    if last is not None:
        last.raise_for_status()
    raise RuntimeError("fetch failed")


def fetch_google_doc_export_by_url(url: str, *, attempts: int = 5) -> str:
    doc_id = extract_google_doc_id(url)
    tab = extract_google_doc_tab_id(url)
    return fetch_google_doc_export_html(doc_id, tab=tab, attempts=attempts)


# ---------------------------------------------------------------------------
# Rules digest (feeds pipeline + Claude context)
# ---------------------------------------------------------------------------


def load_rules_digest(max_chars: int = 6000) -> str:
    if not RULES_FILE.exists():
        return "(cultural_daily_sponsored_rules.md not found on disk)"
    text = RULES_FILE.read_text(encoding="utf-8", errors="replace")
    return text[:max_chars]


# ---------------------------------------------------------------------------
# Decision tree helpers (each returns case id + optional detail)
# ---------------------------------------------------------------------------


def _trace(trace: List[dict], node: str, decision: str, detail: str = "") -> None:
    trace.append({"node": node, "decision": decision, "detail": detail[:500]})


def _normalize_protocol(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("//"):
        return "https:" + u
    return u


def _unwrap_google_redirect(href: str) -> str:
    href = _normalize_protocol((href or "").strip())
    if "google.com/url" in href and "q=" in href:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("q", [""])[0]
        return urllib.parse.unquote(q) or href
    return href


def normalize_href(href: str) -> str:
    """Protocol-relative `//host/...` and one or more Google `url?q=` wrappers."""
    h = _normalize_protocol((href or "").strip())
    for _ in range(4):
        n = _unwrap_google_redirect(h)
        if n == h:
            break
        h = _normalize_protocol(n)
    return h


def _has_inline_color(style: str) -> bool:
    s = (style or "").lower()
    return "color:" in s or "color :" in s


FONT_WEIGHT_BOLD_RE = re.compile(r"font-weight:\s*(700|bold)", re.I)


def _anchor_css_font_bold(a: Tag) -> bool:
    """Google Docs sometimes uses span style font-weight:700 instead of <strong>."""
    for el in a.find_all(True):
        st = el.get("style") or ""
        if FONT_WEIGHT_BOLD_RE.search(st):
            return True
    return False


def classify_image(img: Tag, trace: List[dict]) -> dict:
    """D_IMG_* decision tree for inline <img> in Google Doc export HTML."""
    src = _normalize_protocol((img.get("src") or "").strip())
    if not src:
        _trace(trace, "D_IMG", "D_IMG_01_empty_src", "")
        return {"case": "D_IMG_01_empty_src", "src": "", "alt": None, "w": None, "h": None}

    if src.startswith("data:"):
        _trace(trace, "D_IMG", "D_IMG_02_data_uri", src[:80])
        return {"case": "D_IMG_02_data_uri", "src": src[:120], "alt": img.get("alt"), "w": None, "h": None}

    if "googleusercontent.com" in src or "gstatic.com" in src:
        _trace(trace, "D_IMG", "D_IMG_10_google_hosted", "googleusercontent/gstatic")
        case = "D_IMG_10_google_hosted"
        host = "googleusercontent"
    elif re.match(r"^https?://", src):
        host = urllib.parse.urlparse(src).netloc
        _trace(trace, "D_IMG", "D_IMG_20_external_host", host[:80])
        case = "D_IMG_20_external_host"
    else:
        _trace(trace, "D_IMG", "D_IMG_30_relative_or_odd", src[:120])
        host = "relative_or_unknown"
        case = "D_IMG_30_relative_or_odd"

    w = img.get("width")
    h = img.get("height")
    style = img.get("style") or ""
    if (not w or not h) and style:
        m = re.search(r"width:\s*([\d.]+)px", style, re.I)
        n = re.search(r"height:\s*([\d.]+)px", style, re.I)
        if m:
            w = m.group(1)
        if n:
            h = n.group(1)

    return {
        "case": case,
        "src": src[:500],
        "alt": img.get("alt"),
        "width": w,
        "height": h,
        "host_hint": host,
        "has_inline_color_on_ancestor": False,
    }


def classify_photo_credit_block(el: Tag, trace: List[dict]) -> Optional[dict]:
    """
    D_CRED_* — `<p>` / `<li>` blocks that mention Photo + Pexels (Google Docs export).
    """
    raw_html = str(el)
    text = " ".join(
        el.get_text(" ", strip=True).replace("\xa0", " ").replace("\u200b", "").split()
    )
    if not re.search(r"Photo:\s*.+Pexels", text, re.I):
        return None

    if "<strong>Photo:" in raw_html or re.search(r"<strong>\s*Photo:", raw_html, re.I):
        _trace(trace, "D_CRED", "D_CRED_40_bold_photo", text[:120])
        case = "D_CRED_40_bold_photo"
    elif re.search(
        r"<em>\s*<a[^>]+href=[\"']https?://(?:www\.)?pexels\.com", raw_html, re.I
    ):
        _trace(trace, "D_CRED", "D_CRED_10_em_a_pexels", text[:120])
        case = "D_CRED_10_em_a_pexels"
    elif re.search(r"<a[^>]+pexels\.com[^>]*>.*Photo:", raw_html, re.I):
        _trace(trace, "D_CRED", "D_CRED_20_plain_a_pexels", text[:120])
        case = "D_CRED_20_plain_a_pexels"
    elif re.search(r"pexels\.com", raw_html, re.I):
        _trace(trace, "D_CRED", "D_CRED_30_pexels_mention_noncanonical", text[:120])
        case = "D_CRED_30_pexels_mention_noncanonical"
    else:
        _trace(trace, "D_CRED", "D_CRED_50_photo_text_only", text[:120])
        case = "D_CRED_50_photo_text_only"

    hrefs = [normalize_href(a.get("href") or "") for a in el.find_all("a")]
    hrefs = [h for h in hrefs if h]
    profile_like = any(
        re.search(r"pexels\.com/@", h, re.I) or "/@" in h for h in hrefs
    )
    photo_page = any(re.search(r"pexels\.com/photo/", h, re.I) for h in hrefs)

    return {
        "case": case,
        "text_preview": text[:240],
        "hrefs": hrefs[:8],
        "pexels_profile_like": profile_like,
        "pexels_photo_page": photo_page,
    }


def body_link_shape_canonical(row: dict) -> bool:
    """Canonical sponsored body anchor per site contract (matches pipeline QA)."""
    return bool(
        row.get("has_strong")
        and row.get("target_blank")
        and not row.get("has_nofollow")
        and not row.get("inline_color_on_anchor")
    )


def record_body_anchor(a: Tag, trace: List[dict]) -> Optional[dict]:
    """
    Inventory one <a> for http(s) hrefs only — shape hints for Claude, not a link taxonomy.
    """
    raw_href = (a.get("href") or "").strip()
    href = normalize_href(raw_href)
    if not href or href.startswith("#"):
        _trace(trace, "LINK", "skip_fragment", href[:40])
        return None

    scheme = urllib.parse.urlparse(href).scheme.lower()
    if scheme in ("mailto", "tel", "javascript", "data"):
        _trace(trace, "LINK", "skip_non_http", scheme)
        return None

    rel = (a.get("rel") or []) if isinstance(a.get("rel"), list) else (a.get("rel") or "").split()
    if isinstance(rel, str):
        rel = rel.split()
    rel_s = " ".join(rel) if rel else ""

    inner = a.decode_contents() or ""
    anchor_text = a.get_text(" ", strip=True)[:500]
    strong_inside = (
        bool(a.find("strong"))
        or bool(a.find("b"))
        or "<b>" in inner.lower()
        or _anchor_css_font_bold(a)
    )
    style = a.get("style") or ""
    span_color = any(_has_inline_color(t.get("style", "")) for t in a.find_all("span"))
    inline_color = _has_inline_color(style) or span_color
    has_nofollow = "nofollow" in rel_s.lower()
    target_blank = (a.get("target") or "").lower() == "_blank"

    row = {
        "href": href[:800],
        "anchor_text": anchor_text,
        "has_strong": strong_inside,
        "target_blank": target_blank,
        "has_nofollow": has_nofollow,
        "inline_color_on_anchor": inline_color,
        "rel": rel_s,
    }
    _trace(trace, "LINK", "http", href[:120])
    return row


def analyze_body_structure(soup: BeautifulSoup, full_html: str, trace: List[dict]) -> dict:
    """D_BODY_* — headings, hr, nextpage, donation, tail order heuristics on export HTML."""
    norm = _normalize_gdoc_html(full_html)
    h1 = len(soup.find_all("h1"))
    h2 = len(soup.find_all("h2"))
    h3 = len(soup.find_all("h3"))
    has_hr = bool(soup.find_all("hr")) or "<hr" in norm.lower()
    has_nextpage = "<!--nextpage-->" in norm
    has_donation = (
        "CLICK HERE TO DONATE" in norm
        or "culturaldaily.com/support" in norm.lower()
    )

    cite_em_a = bool(
        re.search(
            r"<(?:p|li|div)[^>]*>\s*<em>\s*<a[^>]+href=[\"']https?://(?:www\.)?pexels\.com",
            norm,
            re.I | re.S,
        )
    )
    cite_loose = bool(re.search(r"Photo:\s*.+via\s+Pexels", norm, re.I))

    last_photo = None
    for m in re.finditer(r"Photo:\s*.+via\s+Pexels", norm, re.I):
        last_photo = m.start()
    hr_pos = norm.lower().find("<hr")
    don_pos = norm.find("CLICK HERE TO DONATE")
    if don_pos < 0:
        don_pos = norm.lower().find("culturaldaily.com/support")

    order_ok: Optional[bool] = None
    if last_photo is not None and hr_pos >= 0 and don_pos >= 0:
        order_ok = last_photo < hr_pos < don_pos

    if has_nextpage:
        _trace(trace, "D_BODY", "D_BODY_50_nextpage_present", "")
    if order_ok is False:
        _trace(trace, "D_BODY", "D_BODY_60_tail_order_bad", f"{last_photo},{hr_pos},{don_pos}")

    return {
        "h1_count": h1,
        "h2_count": h2,
        "h3_count": h3,
        "has_hr": has_hr,
        "has_nextpage": has_nextpage,
        "has_donation_marker": has_donation,
        "citation_em_a_pexels_open": cite_em_a,
        "citation_photo_pexels_text": cite_loose,
        "tail_order_photo_hr_donation_ok": order_ok,
    }


def contract_flags_from_intake(intake: dict) -> List[str]:
    """Map parser output to CD sponsored contract warnings (non-fatal)."""
    flags: List[str] = []
    st = intake.get("body_structure") or {}
    if st.get("has_nextpage"):
        flags.append("source_has_nextpage")
    if st.get("tail_order_photo_hr_donation_ok") is False:
        flags.append("source_tail_order_not_canonical")
    if not st.get("has_hr"):
        flags.append("source_missing_hr")
    if not st.get("has_donation_marker"):
        flags.append("source_missing_donation_marker")

    for c in intake.get("photo_credits") or []:
        if c.get("case") == "D_CRED_40_bold_photo":
            flags.append("source_citation_bold")
        if c.get("case") in ("D_CRED_30_pexels_mention_noncanonical", "D_CRED_50_photo_text_only"):
            flags.append("source_citation_noncanonical_shape")

    for a in intake.get("hyperlinks") or []:
        if a.get("has_nofollow"):
            flags.append("source_body_link_has_nofollow")
        if a.get("inline_color_on_anchor"):
            flags.append("source_body_link_inline_color")
        if not a.get("has_strong"):
            flags.append("source_body_link_missing_bold")
        if not a.get("target_blank"):
            flags.append("source_body_link_missing_target_blank")

    return sorted(set(flags))


def parse_google_doc_intake(
    html: str,
    *,
    source_url: str = "",
    rules_path: Optional[Path] = None,
) -> dict:
    """
    Full intake parse. `source_url` is only used for doc_id + logging context.
    """
    trace: List[dict] = []
    doc_id = ""
    if source_url:
        try:
            doc_id = extract_google_doc_id(source_url)
        except ValueError:
            doc_id = ""

    rules_path = rules_path or RULES_FILE
    rules_digest = load_rules_digest() if rules_path.exists() else ""

    soup = BeautifulSoup(html, "html.parser")

    images: List[dict] = []
    for img in soup.find_all("img"):
        images.append(classify_image(img, trace))

    photo_credits: List[dict] = []
    for tag in soup.find_all(["p", "li"]):
        block = classify_photo_credit_block(tag, trace)
        if block:
            photo_credits.append(block)

    seen_cred: set[tuple[str, str]] = set()
    deduped_credits: List[dict] = []
    for c in photo_credits:
        key = (c.get("text_preview", "")[:120], c.get("case", ""))
        if key in seen_cred:
            continue
        seen_cred.add(key)
        deduped_credits.append(c)
    photo_credits = deduped_credits

    hyperlinks: List[dict] = []
    for a in soup.find_all("a"):
        row = record_body_anchor(a, trace)
        if row is not None:
            hyperlinks.append(row)

    not_canonical = sum(1 for h in hyperlinks if not body_link_shape_canonical(h))

    body_structure = analyze_body_structure(soup, html, trace)

    tab_id = extract_google_doc_tab_id(source_url) if source_url else None

    out = {
        "doc_id": doc_id,
        "source_url": source_url,
        "gdoc_tab_id": tab_id,
        "rules_file": str(rules_path),
        "rules_digest_chars": len(rules_digest),
        "images": images,
        "photo_credits": photo_credits,
        "hyperlinks": hyperlinks,
        "body_structure": body_structure,
        "summary": {
            "image_count": len(images),
            "photo_credit_block_count": len(photo_credits),
            "hyperlink_count": len(hyperlinks),
            "body_links_not_canonical_count": not_canonical,
        },
    }
    out["contract_flags"] = contract_flags_from_intake(out)
    out["decision_trace"] = trace[-400:]
    return out


def intake_json_for_llm(
    intake: dict,
    max_chars: int = 48_000,
    *,
    include_critical_rules_prefix: bool = True,
) -> str:
    """Compact JSON for Anthropic user attachment (drop huge trace if needed).

    When the pipeline already injects `CRITICAL_RULES.md` into the system prompt, pass
    ``include_critical_rules_prefix=False`` to avoid duplicating it in the user JSON (saves tokens).
    """
    slim = {k: v for k, v in intake.items() if k != "decision_trace"}
    slim["decision_trace_tail"] = (intake.get("decision_trace") or [])[-40:]
    s = json.dumps(slim, indent=2, ensure_ascii=False)
    prefix = ""
    if include_critical_rules_prefix and CRITICAL_RULES_FILE.exists():
        cr = CRITICAL_RULES_FILE.read_text(encoding="utf-8", errors="replace")[:14_000]
        prefix = "CRITICAL_RULES_MD_START\n" + cr + "\nCRITICAL_RULES_MD_END\n\n"
    out = prefix + s
    if len(out) > max_chars:
        return out[:max_chars] + "\n…(truncated)…\n"
    return out


# ---------------------------------------------------------------------------
# Batch driver
# ---------------------------------------------------------------------------


def batch_parse_training_docs(
    list_path: Path,
    out_path: Path,
    *,
    fetcher=None,
) -> dict:
    """
    `fetcher(url) -> html` injectable for tests; default is requests Google export.
    """
    def default_fetch(url: str) -> str:
        return fetch_google_doc_export_by_url(url, attempts=5)

    fetch = fetcher or default_fetch
    urls = [
        ln.strip()
        for ln in list_path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    results: List[dict] = []
    errors: List[dict] = []
    for url in urls:
        try:
            html = fetch(url)
            intake = parse_google_doc_intake(html, source_url=url)
            intake["ok"] = True
            results.append({"url": url, "intake": intake})
        except Exception as e:  # noqa: BLE001
            errors.append({"url": url, "error": str(e)[:500]})
    err_summary: Counter = Counter()
    for e in errors:
        msg = e.get("error", "")
        if "401" in msg:
            err_summary["401_unauthorized"] += 1
        elif "403" in msg:
            err_summary["403_forbidden"] += 1
        elif "404" in msg:
            err_summary["404_not_found"] += 1
        elif "410" in msg:
            err_summary["410_gone"] += 1
        elif "429" in msg:
            err_summary["429_rate_limited"] += 1
        elif "500" in msg:
            err_summary["500_server"] += 1
        elif "502" in msg:
            err_summary["502_bad_gateway"] += 1
        elif "503" in msg:
            err_summary["503_unavailable"] += 1
        elif "504" in msg:
            err_summary["504_gateway_timeout"] += 1
        else:
            err_summary["other"] += 1
    report = {
        "doc_count_requested": len(urls),
        "parsed_ok": len(results),
        "errors": errors,
        "error_summary": dict(err_summary),
        "results": results,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main(argv: List[str]) -> None:
    if "--batch" in argv:
        i = argv.index("--batch")
        if i + 1 >= len(argv):
            print("--batch requires a list file path", file=sys.stderr)
            raise SystemExit(2)
        list_path = Path(argv[i + 1])
        out = Path("data/training_parse_report.json")
        if "--out" in argv:
            j = argv.index("--out")
            if j + 1 >= len(argv):
                print("--out requires a path", file=sys.stderr)
                raise SystemExit(2)
            out = Path(argv[j + 1])
        batch_parse_training_docs(list_path, out, fetcher=None)
        print(f"Wrote {out}", file=sys.stderr)
        return
    if len(argv) < 2:
        print(
            "Usage:\n  python doc_parser.py <google-doc-url>\n"
            "  python doc_parser.py --batch data/training_docs.txt [--out path]",
            file=sys.stderr,
        )
        raise SystemExit(2)
    url = argv[1]
    html = fetch_google_doc_export_by_url(url, attempts=5)
    intake = parse_google_doc_intake(html, source_url=url)
    print(json.dumps(intake, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv)
