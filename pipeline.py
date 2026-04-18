"""
Scoutmonkeys publishing pipeline: Google Doc → WordPress draft (CD / DCR) with QA + Twilio WhatsApp.

Environment variables are documented in CLAUDE.md. Run:

    python pipeline.py "<google doc url>" cd
"""
from __future__ import annotations

import html as html_module
import io
import json
import math
import os
import re
import sys
import textwrap
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, Comment, NavigableString
from PIL import Image, ImageOps

import doc_parser

REPO_ROOT = Path(__file__).resolve().parent
CRITICAL_RULES_PATH = REPO_ROOT / "CRITICAL_RULES.md"
OUR_FRIENDS_AUDIT_JSON = REPO_ROOT / "data" / "our_friends_audit.json"
AUDIT_FORMAT_PROFILE_JSON = REPO_ROOT / "data" / "audit_format_profile.json"

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "")

TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "")
WA_TO = os.environ.get("WHATSAPP_TO")
WA_PHONE = os.environ.get("WHATSAPP_PHONE", "+5215549571586")

OUR_FRIENDS_AUTHOR_ID = int(os.environ.get("OUR_FRIENDS_AUTHOR_ID", "19"))


def _refresh_runtime_env_from_os() -> None:
    """Re-read env-backed module globals (needed after `_apply_repo_dotenv_for_cli()`)."""
    global ANTHROPIC_KEY, ANTHROPIC_MODEL, PEXELS_KEY
    global TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM, WA_TO, WA_PHONE
    ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "")
    TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
    TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
    TWILIO_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "")
    WA_TO = os.environ.get("WHATSAPP_TO")
    WA_PHONE = os.environ.get("WHATSAPP_PHONE", "+5215549571586")


# ---------------------------------------------------------------------------
# Site configuration
# ---------------------------------------------------------------------------


def _site_cd() -> dict:
    return {
        "key": "cd",
        "site_label": "Cultural Daily",
        "prefix": "CD",
        "wp_url": os.environ.get("WP_URL", "https://www.culturaldaily.com").rstrip("/"),
        "wp_user": os.environ.get("WP_USER", ""),
        "wp_pass": os.environ.get("WP_PASS", ""),
        "hero_w": 975,
        "hero_h": 250,
        "social_w": 1920,
        "social_h": 1400,
        "title_max": 60,
        "seo_title_max": 60,
        "author_id": OUR_FRIENDS_AUTHOR_ID,
    }


def _site_dcr() -> dict:
    return {
        "key": "dcr",
        "site_label": "Daily Cheltenham Review",
        "prefix": "DCR",
        "wp_url": os.environ.get("DCR_WP_URL", "").rstrip("/"),
        "wp_user": os.environ.get("DCR_WP_USER", ""),
        "wp_pass": os.environ.get("DCR_WP_PASS", ""),
        "hero_w": int(os.environ.get("DCR_HERO_W", "1200")),
        "hero_h": int(os.environ.get("DCR_HERO_H", "675")),
        "social_w": int(os.environ.get("DCR_SOCIAL_W", "1200")),
        "social_h": int(os.environ.get("DCR_SOCIAL_H", "630")),
        "title_max": 65,
        "seo_title_max": 65,
        "author_id": int(os.environ.get("DCR_AUTHOR_ID", "1")),
    }


SITES: Dict[str, dict] = {"cd": _site_cd()}


def _refresh_sites() -> None:
    global SITES
    SITES = {"cd": _site_cd()}
    d = _site_dcr()
    if d["wp_url"] and d["wp_user"] and d["wp_pass"]:
        SITES["dcr"] = d


_refresh_sites()

# Canonical CD donation CTA (exact anchor text — CRITICAL_RULES / operator contract)
DONATION_CTA_TEXT_CD = (
    "CLICK HERE TO DONATE IN SUPPORT OF OUR NONPROFIT COVERAGE OF ARTS AND CULTURE"
)
DONATION_HTML_CD = (
    "<p><strong>"
    f'<a href="https://www.culturaldaily.com/support/" target="_blank" rel="nofollow noopener">'
    f"{DONATION_CTA_TEXT_CD}</a>"
    "</strong></p>"
)

# CD AIOSEO title suffix when length allows (never mid-word truncation — see ``build_cd_aioseo_seo_title``).
CD_SEO_TITLE_SUFFIX = " | Cultural Daily"
META_DESCRIPTION_MIN = 120
META_DESCRIPTION_MAX = 160


def donation_html_for(site: dict) -> str:
    if site["key"] == "cd":
        return DONATION_HTML_CD
    custom = os.environ.get("DCR_DONATION_HTML", "").strip()
    if custom:
        return custom
    return (
        '<p><strong><a href="#" target="_blank" rel="nofollow noopener">'
        "CLICK HERE TO DONATE NOW!</a></strong></p>"
    )


# ---------------------------------------------------------------------------
# Google Doc
# ---------------------------------------------------------------------------


def gdoc_id_from_url(url: str) -> str:
    try:
        return doc_parser.extract_google_doc_id(url)
    except ValueError as exc:
        raise ValueError(f"Could not parse Google Doc id from URL: {url}") from exc


def fetch_gdoc_html(doc_url: str) -> str:
    """
    Public Google Doc HTML export. Delegates to ``doc_parser`` so ``?tab=t.0`` (and other ``tab=``)
    query parameters from edit URLs are forwarded to ``/export?format=html``.
    """
    tab = doc_parser.extract_google_doc_tab_id(doc_url)
    if tab:
        print(f"[1a] Export tab parameter: {tab!r}")
    return doc_parser.fetch_google_doc_export_by_url(doc_url, attempts=5)


# ---------------------------------------------------------------------------
# Anthropic (Messages API)
# ---------------------------------------------------------------------------


def _anthropic_messages(system: str, user: str, *, temperature: float = 0.2) -> str:
    if not ANTHROPIC_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 12000,
        "temperature": temperature,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    headers = {
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    for attempt in range(10):
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
                timeout=300,
            )
        except requests.RequestException as exc:
            if attempt + 1 < 10:
                w = min(90, 25 + attempt * 10)
                print(f"[anthropic] network error ({exc!r}) — sleeping {w}s then retry ({attempt + 1}/9)…")
                time.sleep(w)
                continue
            raise
        if r.status_code == 429 and attempt + 1 < 10:
            wait = min(180, 45 + attempt * 20)
            print(f"[anthropic] 429 rate limit — sleeping {wait}s then retry ({attempt + 1}/9)…")
            time.sleep(wait)
            continue
        if r.status_code in (502, 503, 504) and attempt + 1 < 10:
            print(f"[anthropic] HTTP {r.status_code} — sleeping 30s then retry…")
            time.sleep(30)
            continue
        if not r.ok:
            raise RuntimeError(f"Anthropic API error {r.status_code}: {r.text[:800]}")
        data = r.json()
        return data["content"][0]["text"]
    raise RuntimeError("Anthropic API error 429 after retries")


def _extract_json_blob(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        return json.loads(m.group(1))
    m2 = re.search(r"\{[\s\S]*\}\s*$", text)
    if m2:
        return json.loads(m2.group(0))
    raise ValueError("Anthropic response did not contain JSON")


def _default_plan_system(site: dict) -> str:
    base = textwrap.dedent(
        f"""
        You are the Scoutmonkeys sponsored-content planner for {site["site_label"]}.

        The WordPress article body HTML is never produced by you: the pipeline injects it directly from
        the Google Doc HTML export. You only see a plaintext excerpt (DOC_PLAINTEXT_EXCERPT) for context
        to choose topic_slug, titles, SEO fields, focus keyword, and hero_pexels_query.

        HARD RULES:
        1) Outbound body link shape (<a href target=_blank><strong>…</strong></a>, dofollow) is enforced
           in code on the Doc HTML — do not try to output body HTML.
        2) If the Doc has no usable client hero image (see MACHINE_CLIENT_IMAGE_SRC when present), you MUST
           still output a strong hero_pexels_query (3–8 words) so the pipeline can source a Pexels banner.

        Output ONLY a JSON object (no markdown fences). Do NOT include article_body_html (omit the key
        entirely, or set it to an empty string — the pipeline ignores it).

        Keys (all strings except as noted):
        - topic_slug: lowercase kebab-case, ascii, based on the article topic
        - post_title: concise H1-style title (CRITICAL runs may override from machine extract)
        - focus_keyword: short phrase for SEO
        - seo_title: <= 60 characters; hint for AIOSEO (pipeline may re-fit to word boundaries and suffix).
        - meta_description: 120–160 characters inclusive, plain text, grounded in the excerpt only
        - hero_pexels_query: 3-8 word Pexels search query (required when no client hero)
        - hero_image_alt: 12–160 characters; plain description of what is visible in the hero/social photo
          (not the article headline, not a repeat of the H1)
        - photographer_fallback_name: string (Pexels credit hint; may be empty)
        - category_hint: on CD, WordPress category lane (e.g. "Check This Out", "casino", "grey niche"); on
          other sites, hints like "travel", "film", "books" still apply where relevant

        If MACHINE_INTAKE_JSON is present, it lists each http(s) anchor with shape hints only
        (bold, target_blank, nofollow, inline color) — use it to understand topics and URLs, not to emit HTML.
        """
    ).strip()
    if site.get("key") == "cd":
        base = (
            base
            + "\n\n"
            + textwrap.dedent(
                """
                AUDIT CONFORMITY (Cultural Daily): When `data/our_friends_audit.json` exists, it is the
                empirical corpus of Our Friends posts (see CRITICAL_RULES.md §13). Do not invent novel
                HTML layout patterns for the article body — you do not emit body HTML; the pipeline does.
                """
            ).strip()
        )
    return base


def plan_from_gdoc_html(
    site: dict,
    gdoc_html: str,
    intake: Optional[dict] = None,
    *,
    critical_rules: bool = False,
    machine_h1: str = "",
    client_image_src: Optional[str] = None,
) -> dict:
    base = _default_plan_system(site)
    critical_block = load_critical_rules_text() if critical_rules else ""
    if critical_rules and critical_block:
        system = (
            "THE FOLLOWING CRITICAL_RULES.md OVERRIDES ANY CONFLICTING INSTRUCTION BELOW.\n\n"
            + critical_block
            + "\n\n--- SUBORDINATE: JSON + TECHNICAL CONSTRAINTS ---\n\n"
            + base
            + textwrap.dedent(
                """

                When CRITICAL_RULES applies to this run (this message includes the file above):
                - post_title MUST match MACHINE_EXTRACTED_H1 exactly (character-for-character).
                - Do NOT output article_body_html — the pipeline builds the article body only from the Doc HTML.
                - Do NOT alter donation text — the pipeline appends the canonical donation block; never invent or rewrite it.
                - focus_keyword: **1 word from the title when possible** (sometimes 2); short core subject (never the full H1); pipeline enforces internal content score ≥82 for CD QA.
                - seo_title: optional AIOSEO hint (<=60 chars); the pipeline builds the final AIOSEO title from the H1 with word-safe clipping and optional `` | Cultural Daily`` suffix.
                - meta_description: 120–160 chars; use only wording supported by the plaintext excerpt (no new factual claims).
                - hero_image_alt: required short visual description of the hero photograph (never the H1 string).
                - category_hint: set a **specific** WordPress lane when obvious (e.g. ``casino``, ``grey niche``,
                  kebab-case slug hints). Use **Check This Out** only for generic arts/culture Our Friends pieces.
                  The pipeline **also** infers lanes from title + topic_slug + excerpt when the hint is generic,
                  so you need not repeat obvious verticals. **Never** Featured Story or Sponsored.
                - If MACHINE_CLIENT_IMAGE_SRC is not "(none)", set hero_pexels_query to "" (empty string).
                - Social image is mandatory: the pipeline always generates and sets OG/social; never omit.
                - Social output must be exactly 1920×1400 pixels (handled by the pipeline resize — do not suggest other sizes).
                - AUDIT CONFORMITY (§13): Do not introduce HTML structures or formatting patterns that are
                  not evidenced in data/our_friends_audit.json (Our Friends corpus). Prefer preserving the
                  Doc's tags; only apply patterns known from that audit + cultural_daily_sponsored_rules.md.
                """
            ).strip()
        )
    else:
        system = base

    machine = ""
    if intake:
        machine = (
            "\nMACHINE_INTAKE_JSON_START\n"
            + doc_parser.intake_json_for_llm(
                intake,
                max_chars=16_000 if critical_rules else 50_000,
                include_critical_rules_prefix=not critical_rules,
            )
            + "\nMACHINE_INTAKE_JSON_END\n"
        )

    # Never send raw Doc HTML to Claude (rule 2 — body must not pass through the model).
    excerpt_max = 12_000 if critical_rules else 28_000
    excerpt = planner_plaintext_excerpt_from_gdoc(gdoc_html, max_chars=excerpt_max)
    user_parts = [
        "DOC_PLAINTEXT_EXCERPT_START\n",
        excerpt,
        "\nDOC_PLAINTEXT_EXCERPT_END\n",
    ]
    if critical_rules and len(excerpt) >= excerpt_max:
        user_parts.append(
            f"\nNOTE: Plaintext excerpt was clipped to {excerpt_max} characters for API limits.\n"
        )
    if critical_rules:
        user_parts.append(
            "\nMACHINE_EXTRACTED_H1 (post_title MUST match exactly):\n"
            + (machine_h1 or "(empty — extract from HTML failed)")
            + "\n"
        )
        user_parts.append(
            "\nMACHINE_CLIENT_IMAGE_SRC:\n"
            + planner_client_image_src_excerpt_for_llm(client_image_src)
            + "\n"
        )
    if critical_rules and site.get("key") == "cd":
        user_parts.append("\n" + audit_conformity_machine_note() + "\n")
    user_parts.append(machine)
    user_parts.append("\nReturn JSON only.")
    user = "".join(user_parts)

    temp = 0.05 if critical_rules else 0.2
    raw = _anthropic_messages(system, user, temperature=temp)
    try:
        return _extract_json_blob(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        fix_system = (
            "You repair JSON. Output ONLY one valid JSON object (no markdown fences), "
            "same keys as the Scoutmonkeys planner: topic_slug, post_title, focus_keyword, seo_title, "
            "meta_description, hero_pexels_query, hero_image_alt, photographer_fallback_name, category_hint. "
            "Do NOT include article_body_html (omit or empty string). "
            "If CRITICAL_RULES applied, post_title must match MACHINE_EXTRACTED_H1 and hero_pexels_query may be empty when client image exists. "
            "On Cultural Daily, seo_title is rebuilt in the pipeline from the H1 (word-safe, max 60 chars)."
        )
        fix_user = (
            "The text below was meant to be one JSON object but it is invalid JSON. "
            f"Parse error: {exc}\n\nTEXT:\n{raw[:180_000]}"
        )
        raw2 = _anthropic_messages(fix_system, fix_user, temperature=temp)
        return _extract_json_blob(raw2)


# ---------------------------------------------------------------------------
# Pexels + imaging
# ---------------------------------------------------------------------------


def pexels_search(query: str, per_page: int = 15) -> List[dict]:
    if not PEXELS_KEY:
        raise RuntimeError("PEXELS_API_KEY is not set")
    r = requests.get(
        "https://api.pexels.com/v1/search",
        headers={"Authorization": PEXELS_KEY},
        params={"query": query, "per_page": per_page},
        timeout=60,
    )
    r.raise_for_status()
    return r.json().get("photos", [])


def resolve_hero_pexels_photo(
    site: dict, title: str, topic_slug: str, hero_q: str
) -> Tuple[dict, str]:
    """
    Always return a Pexels photo dict and the query that produced it.
    The client Doc may have zero images; the pipeline still must ship a hero from Pexels.
    """
    target_ratio = site["hero_w"] / site["hero_h"]
    buckets: List[str] = []
    q0 = (hero_q or "").strip()
    if q0:
        buckets.append(q0)
    buckets.append((title or topic_slug or "culture arts").strip())
    slug_words = " ".join(w for w in (topic_slug or "").split("-") if len(w) > 2)
    if slug_words:
        buckets.append(slug_words)
    buckets.extend(
        [
            "wide city skyline banner",
            "culture festival crowd",
            "theater stage lights audience",
            "art museum gallery interior",
            "music concert crowd night",
        ]
    )
    seen: set[str] = set()
    tried: List[str] = []
    for q in buckets:
        q = re.sub(r"\s+", " ", q).strip()[:100]
        if len(q) < 2 or q.lower() in seen:
            continue
        seen.add(q.lower())
        tried.append(q)
        photos = pexels_search(q)
        if not photos:
            continue
        try:
            return _pexels_pick_hero(photos, target_ratio), q
        except RuntimeError:
            continue
    raise RuntimeError(
        "Pipeline requires a Pexels hero image (set PEXELS_API_KEY and ensure Pexels returns results). "
        f"Tried queries: {', '.join(tried[:12]) or '(none)'}"
    )


def _pexels_pick_hero(photos: List[dict], target_ratio: float) -> dict:
    best = None
    best_score = 1e9
    for p in photos:
        src = p.get("src") or {}
        url = src.get("large2x") or src.get("original") or src.get("large")
        if not url:
            continue
        w, h = int(p.get("width") or 0), int(p.get("height") or 0)
        if w <= 0 or h <= 0:
            continue
        ratio = w / h
        score = abs(ratio - target_ratio)
        if score < best_score:
            best_score = score
            best = p
    if not best:
        raise RuntimeError("No usable Pexels results for hero")
    return best


def _download_image(url: str) -> Image.Image:
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def _resize_cover_exact_floor(img: Image.Image, tw: int, th: int) -> Image.Image:
    """
    Cover-crop to exact ``tw``×``th`` using Pillow ``ImageOps.fit``.

    Target dimensions use ``int(math.floor(...))`` only — never round up — so outputs stay
    exactly **975×250** / **1920×1400** (avoids off-by-one e.g. 1920×1401).
    """
    tw = max(1, int(math.floor(float(tw))))
    th = max(1, int(math.floor(float(th))))
    return ImageOps.fit(img, (tw, th), method=Image.Resampling.LANCZOS)


def build_resized_pair(site: dict, hero_photo: dict) -> Tuple[Image.Image, Image.Image]:
    src = hero_photo.get("src") or {}
    url = src.get("original") or src.get("large2x") or src.get("large")
    img = _download_image(url)
    hero = _resize_cover_exact_floor(img, site["hero_w"], site["hero_h"])
    social = _resize_cover_exact_floor(img, site["social_w"], site["social_h"])
    return hero, social


def critical_rules_active() -> bool:
    return CRITICAL_RULES_PATH.is_file()


def load_critical_rules_text(max_chars: int = 28_000) -> str:
    if not critical_rules_active():
        return ""
    return CRITICAL_RULES_PATH.read_text(encoding="utf-8", errors="replace")[:max_chars]


def audit_conformity_machine_note() -> str:
    """
    CRITICAL_RULES §13 — remind the planner that output must conform to the audited Our Friends corpus.
    Does not load the full JSON (large); uses post_count from the file header when present.
    """
    p = OUR_FRIENDS_AUDIT_JSON
    if not p.is_file():
        return (
            "MACHINE_AUDIT_CONFORMITY: data/our_friends_audit.json is not present in this checkout. "
            "Do not invent novel HTML/layout patterns; use only structures implied by "
            "cultural_daily_sponsored_rules.md + existing pipeline tail contracts. If unsure, preserve "
            "the Google Doc markup and flag for manual review."
        )
    try:
        head = p.read_text(encoding="utf-8", errors="replace")[:24_000]
        m = re.search(r'"post_count"\s*:\s*(\d+)', head)
        n = m.group(1) if m else "3208"
    except OSError:
        n = "3208"
    return (
        f"MACHINE_AUDIT_CONFORMITY: Canonical audited corpus is {p.as_posix()} "
        f"(Cultural Daily Our Friends, post_count={n}). "
        "CRITICAL_RULES §13: every formatting pattern, HTML structure, and content element you emit "
        "must be something that could appear in that dataset — do not introduce new constructs. "
        "When uncertain, assume the Doc's existing tags are the source of truth and only apply "
        "minimal fixes already used in audited posts (e.g. paid link wrapper shape from rules)."
    )


def extract_h1_from_gdoc_html(ghtml: str) -> str:
    soup = BeautifulSoup(ghtml, "html.parser")
    h1 = soup.find("h1")
    if h1:
        t = h1.get_text(" ", strip=True)
        if t:
            return t
    for p in soup.find_all("p"):
        cls = " ".join(p.get("class") or []).lower()
        if "title" in cls:
            t = p.get_text(" ", strip=True)
            if t:
                return t
    tit = soup.find("title")
    if tit and tit.string:
        return tit.string.split(" - ")[0].strip()
    return ""


def first_client_image_src_from_gdoc(ghtml: str) -> Optional[str]:
    soup = BeautifulSoup(ghtml, "html.parser")
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        if not src or src.startswith("blob:"):
            continue
        if src.startswith(("http://", "https://", "data:")):
            return src
    return None


def planner_client_image_src_excerpt_for_llm(src: Optional[str]) -> str:
    """
    Google Docs often exports the lead image as a multi-megabyte ``data:…;base64,…`` URI.
    That must never be pasted into the Anthropic planner prompt (token limit).
    """
    if not src:
        return "(none)"
    s = src.strip()
    if s.startswith("data:"):
        meta, sep, _rest = s.partition(",")
        return (
            f"{meta}{sep}[base64 omitted; inline image length={len(s)} chars — "
            "pipeline still loads this as the client hero]"
        )
    if len(s) > 2000:
        return s[:2000] + f"...[truncated; total {len(s)} chars]"
    return s


def _url_key(u: str) -> str:
    u = (u or "").strip().split("?", 1)[0].strip().lower()
    return u.rstrip("/")


def remove_client_hero_image_from_body_html(body_html: str, client_src: str) -> str:
    """Hero image is featured_media only — strip the same ``<img>`` from the article body (CD rule)."""
    if not body_html or not (client_src or "").strip():
        return body_html
    soup = BeautifulSoup(body_html, "html.parser")
    ck = _url_key(client_src)
    for img in list(soup.find_all("img")):
        if _url_key(img.get("src") or "") != ck:
            continue
        parent = img.parent
        img.decompose()
        if parent and getattr(parent, "name", "") == "p":
            if not parent.get_text(strip=True) and not parent.find("img"):
                parent.decompose()
        break
    return str(soup)


def _norm_title_text(t: str) -> str:
    t = unicodedata.normalize("NFKC", t or "")
    return re.sub(r"\s+", " ", t).strip().lower()


def _cd_inline_alt_for_img(
    alt_raw: str, post_title: str, *, slot: Optional[int] = None
) -> str:
    """Inline ``alt``: short photo description — never the article title."""
    tnorm = _norm_title_text(post_title)
    pa = unicodedata.normalize("NFKC", (alt_raw or "").strip())
    if pa and _norm_title_text(pa) != tnorm and 8 <= len(pa) <= 180:
        return pa[:180]
    if slot is not None:
        return f"Inline photograph {slot} for this sponsored article"
    return "Photograph supporting this sponsored article"


def _merge_css_style(prev: Optional[str], add: str) -> str:
    prev = (prev or "").strip().rstrip(";")
    add = (add or "").strip().rstrip(";")
    if not prev:
        return add
    if not add:
        return prev
    return f"{prev};{add}"


def _urls_loosely_same(a: str, b: str) -> bool:
    return _url_key(a) == _url_key(b)


def _cd_remove_img_and_collapsing_empties(img) -> None:
    """Remove an ``<img>`` and drop now-empty inline wrappers / spacer paragraphs."""
    parent = getattr(img, "parent", None)
    img.decompose()
    el = parent
    for _ in range(12):
        if el is None or not getattr(el, "name", None):
            break
        name = el.name
        if name in ("span", "b", "i", "em", "strong"):
            if el.get_text(strip=True) or el.find("img"):
                break
            nxt = el.parent
            el.decompose()
            el = nxt
            continue
        if name == "p":
            if el.get_text(strip=True) or el.find("img"):
                break
            nxt = el.parent
            el.decompose()
            el = nxt
            continue
        break


def cd_deduplicate_inline_body_images(html: str, *, hero_src_to_skip: str = "") -> str:
    """
    Remove duplicate inline images (same normalized URL as an earlier ``<img>``) and any
    body copy of the featured hero URL. Typical Google Docs issue: the same photo pasted twice.
    First occurrence in document order is kept.
    """
    if not (html or "").strip():
        return html
    soup = BeautifulSoup(html, "html.parser")
    hero_k = _url_key(hero_src_to_skip) if (hero_src_to_skip or "").strip() else ""
    seen: set[str] = set()
    changed = False
    for img in list(soup.find_all("img")):
        src = (img.get("src") or "").strip()
        if not src.lower().startswith("http"):
            continue
        k = _url_key(src)
        if hero_k and k == hero_k:
            _cd_remove_img_and_collapsing_empties(img)
            changed = True
            continue
        if k in seen:
            _cd_remove_img_and_collapsing_empties(img)
            changed = True
            continue
        seen.add(k)
    return str(soup) if changed else html


def strip_duplicate_lead_title_from_body_html(body_html: str, h1_text: str) -> str:
    """H1 lives only in the WordPress title field — remove duplicate lead heading/body title (CD+CRITICAL)."""
    if not body_html or not (h1_text or "").strip():
        return body_html
    want = _norm_title_text(h1_text)
    soup = BeautifulSoup(body_html, "html.parser")
    h1 = soup.find("h1")
    if h1 and _norm_title_text(h1.get_text(" ", strip=True)) == want:
        h1.decompose()
    else:
        first_p = soup.find("p")
        if first_p:
            cls = " ".join(first_p.get("class") or []).lower()
            if "title" in cls and _norm_title_text(first_p.get_text(" ", strip=True)) == want:
                first_p.decompose()
            elif _norm_title_text(first_p.get_text(" ", strip=True)) == want:
                first_p.decompose()
    return str(soup)


def _p_is_gdoc_spacing_only(par: Any) -> bool:
    """
    True when ``<p>`` contributes no visible text — Google Docs often emits empty
    paragraphs, ``<p><br></p>``, ``<p>&nbsp;</p>``, or empty wrapped spans that
    create double spacing in WordPress.
    """
    if par is None or getattr(par, "name", "") != "p":
        return False
    if par.find("img"):
        return False
    text = par.get_text(separator="", strip=False)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\xa0", " ").replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"[\s\u00a0]+", "", text)
    return len(text) == 0


def normalize_cd_body_vertical_spacing(html: str) -> str:
    """
    Google Doc → WordPress spacing hygiene (run early on CD body HTML):

    1. Remove empty ``<p></p>`` and spacer paragraphs (``<br>`` / ``&nbsp;`` only).
    2. Collapse consecutive blank lines in the serialized HTML string.

    Does not alter paragraphs that contain real text or non-empty links.
    """
    if not html:
        return html
    soup = BeautifulSoup(html, "html.parser")
    for _ in range(50):
        removed = False
        for p in list(soup.find_all("p")):
            if _p_is_gdoc_spacing_only(p):
                p.decompose()
                removed = True
        if not removed:
            break
    s = str(soup)
    s = re.sub(r">\s*\n(?:\s*\n){2,}\s*<", ">\n\n<", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"(</p>)\s*\n\s*\n\s*\n+", r"\1\n\n", s)
    s = re.sub(r"(</h[12]>)\s*\n\s*\n\s*\n+", r"\1\n\n", s)
    return s


def cd_promote_gdoc_heading_paragraphs(html: str) -> str:
    """
    Google Docs often exports **section titles** as a single styled ``<p><span class=…>Title</span></p>``
    while true subheads use ``<h3><span class=…>``. Published Our Friends HTML (audit corpus) uses
    ``<h2>`` for those section tiers. We learn span-class **fingerprints** from existing ``h2``–``h6``
    in the same export, then promote short, link-free ``<p>`` blocks that match — document-relative,
    so per-doc ``c1`` / ``c4`` ids do not need to be hard-coded.
    """
    if not (html or "").strip():
        return html
    soup = BeautifulSoup(html, "html.parser")
    span_fps: set[tuple[str, ...]] = set()
    for hx in soup.find_all(["h2", "h3", "h4", "h5", "h6"]):
        spans = [c for c in hx.children if getattr(c, "name", "") == "span"]
        if len(spans) == 1 and (spans[0].get("class") or []):
            cl = spans[0].get("class") or []
            if isinstance(cl, list):
                span_fps.add(tuple(sorted(str(x) for x in cl if x)))
        else:
            sp0 = hx.find("span", recursive=False)
            if sp0 and (sp0.get("class") or []):
                cl = sp0.get("class") or []
                if isinstance(cl, list):
                    span_fps.add(tuple(sorted(str(x) for x in cl if x)))
    if not span_fps:
        return str(soup)

    promoted = 0
    for p in list(soup.find_all("p")):
        if p.find_parent("li"):
            continue
        if p.find("a") or p.find("img"):
            continue
        elems = [c for c in p.children if getattr(c, "name", None)]
        if len(elems) != 1 or elems[0].name != "span":
            continue
        sp = elems[0]
        cl = sp.get("class") or []
        if not isinstance(cl, list):
            continue
        fp = tuple(sorted(str(x) for x in cl if x))
        if fp not in span_fps:
            continue
        tx = p.get_text(" ", strip=True)
        if len(tx) < 8 or len(tx) > 220:
            continue
        if re.match(r"^photo\s*:", tx, re.I):
            continue
        h2 = soup.new_tag("h2")
        for child in list(p.contents):
            h2.append(child)
        p.replace_with(h2)
        promoted += 1

    if promoted:
        print(
            f"[2b] CD audit align: promoted {promoted} GDoc section "
            f"<p> block(s) to <h2> (matched heading span classes from this Doc's h2–h6)."
        )
    return str(soup)


_FOOTNOTE_DEF_ANCHOR_ID_RE = re.compile(r"^cmnt\d+$", re.I)
_FOOTNOTE_DEF_ANCHOR_HREF_RE = re.compile(r"^#cmnt_ref\d+$", re.I)


def _cd_url_looks_like_inline_image(url: str) -> bool:
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        return False
    low = u.lower()
    if "imrs.php" in low:
        return True
    base = u.split("?", 1)[0].lower()
    return bool(re.search(r"\.(jpe?g|png|webp|gif)$", base, re.I))


def cd_resolve_gdoc_footnote_images(html: str) -> str:
    """
    Google Docs often stores image credits as **footnotes**: inline markers such as
    ``<sup><a href="#cmnt1" id="cmnt_ref1">[a]</a></sup>`` and definitions at the **end** of the
    export body: ``<a href="#cmnt_ref1" id="cmnt1">[a]</a><span>https://…jpg…</span>``.

    Those footnote blocks paste as ugly blue ``[a]`` links and duplicate URLs after the article.
    This pass resolves each ``#cmntN`` marker to a real ``<img src="https…">`` (so
    :func:`cd_reupload_inline_body_images` can upload them) and **removes** the footnote definition
    paragraphs from the HTML.
    """
    if not (html or "").strip():
        return html
    soup = BeautifulSoup(html, "html.parser")
    url_by_cmnt: dict[str, str] = {}
    def_ps: List[Any] = []

    for a in list(soup.find_all("a", id=True, href=True)):
        aid = str(a.get("id") or "").strip()
        if not _FOOTNOTE_DEF_ANCHOR_ID_RE.match(aid):
            continue
        if not _FOOTNOTE_DEF_ANCHOR_HREF_RE.match(str(a.get("href") or "").strip()):
            continue
        raw_u = ""
        sp = a.find_next_sibling()
        if sp is not None and getattr(sp, "name", "") == "span":
            raw_u = html_module.unescape(re.sub(r"\s+", "", (sp.get_text() or "").strip()))
        if not _cd_url_looks_like_inline_image(raw_u):
            par = a.find_parent("p")
            if par is not None:
                for s in par.find_all("span"):
                    cand = html_module.unescape(re.sub(r"\s+", "", (s.get_text() or "").strip()))
                    if cand.startswith("http") and _cd_url_looks_like_inline_image(cand):
                        raw_u = cand
                        break
        if not _cd_url_looks_like_inline_image(raw_u):
            continue
        url_by_cmnt[aid.lower()] = raw_u
        p = a.find_parent("p")
        if p is not None and p not in def_ps:
            def_ps.append(p)

    for p in def_ps:
        p.decompose()

    replaced = 0
    for a in list(soup.find_all("a", href=True)):
        href = str(a.get("href") or "").strip()
        m = re.fullmatch(r"#(cmnt\d+)", href, flags=re.I)
        if not m:
            continue
        key = m.group(1).lower()
        url = url_by_cmnt.get(key)
        if not url:
            continue
        img = soup.new_tag("img", src=url, alt="")
        parent = a.parent
        if parent is not None and getattr(parent, "name", "") == "sup":
            strays = [
                x
                for x in parent.contents
                if isinstance(x, NavigableString) and str(x).strip()
            ]
            elems = [x for x in parent.children if getattr(x, "name", None)]
            if not strays and len(elems) == 1 and elems[0] is a:
                parent.replace_with(img)
                replaced += 1
                continue
        a.replace_with(img)
        replaced += 1

    for a in list(soup.find_all("a", href=True)):
        href = str(a.get("href") or "").strip()
        if not re.fullmatch(r"#cmnt\d+", href, flags=re.I):
            continue
        tx = a.get_text(strip=True)
        if not re.match(r"^\[[a-zA-Z0-9]+\]$", tx):
            continue
        sup = a.parent
        if sup is not None and getattr(sup, "name", "") == "sup":
            sup.decompose()
        else:
            a.decompose()

    for dv in list(soup.find_all("div")):
        if dv.find(True) is None and not (dv.get_text() or "").strip():
            dv.decompose()

    if replaced:
        print(
            f"[2c] Google Doc footnotes: replaced {replaced} [a]/[b]/… marker(s) with <img> "
            f"and removed {len(def_ps)} footnote URL block(s) from the body."
        )
    return str(soup)


def cd_strip_residual_footnote_url_paragraphs(html: str) -> str:
    """
    Drop ``<p>`` nodes that are only a GDoc-style marker plus a bare image URL (common when the
    footnote definition block layout does not match ``#cmntN`` + sibling ``<span>``).
    """
    if not (html or "").strip():
        return html
    soup = BeautifulSoup(html, "html.parser")
    killed = 0
    for p in list(soup.find_all("p")):
        t = p.get_text(" ", strip=True)
        if re.match(r"^\[[a-zA-Z0-9]+\]\s*https?://", t):
            p.decompose()
            killed += 1
    if killed:
        print(f"[2c] Google Doc footnotes: removed {killed} residual [x]+URL paragraph(s).")
    return str(soup)


def _cd_first_substantial_body_paragraph(soup: BeautifulSoup, after_p) -> Any:
    """First later ``<p>`` that looks like real article prose (not a caption / credit stub)."""
    seen = False
    fallback: List[Any] = []
    for p in soup.find_all("p"):
        if p is after_p:
            seen = True
            continue
        if not seen:
            continue
        t = p.get_text(" ", strip=True)
        if t.lower().startswith("photo:"):
            continue
        if len(t) >= 80:
            return p
        if len(t) >= 50 and max(t.count("."), t.count("!"), t.count("?")) >= 1:
            return p
        if len(t) >= 45:
            fallback.append(p)
    return fallback[0] if fallback else None


def cd_relocate_lead_images_after_substantive_opening(html: str, *, used_client_hero: bool) -> str:
    """
    When the client hero was stripped from the body, GDoc footnote inserts often remain in the
    **first** ``<p>`` as bare ``<img>`` rows before the real opener — unlike published Our Friends
    HTML, which leads with prose. Move those lead-only images to **after** the first substantial
    paragraph.
    """
    if not (html or "").strip() or not used_client_hero:
        return html
    soup = BeautifulSoup(html, "html.parser")
    ps = soup.find_all("p")
    if not ps:
        return str(soup)
    p0 = ps[0]
    imgs = [
        im
        for im in p0.find_all("img")
        if (im.get("src") or "").strip().lower().startswith("http")
    ]
    if not imgs:
        return str(soup)
    t0 = p0.get_text(" ", strip=True)
    if len(t0) >= 72:
        return str(soup)
    anchor = _cd_first_substantial_body_paragraph(soup, p0)
    if anchor is None:
        return str(soup)
    prev = anchor
    n_moved = 0
    for im in list(imgs):
        np = soup.new_tag("p")
        np.append(im.extract())
        prev.insert_after(np)
        prev = np
        n_moved += 1
    if not p0.get_text(strip=True) and not p0.find("img"):
        p0.decompose()
    if n_moved:
        print(
            "[2c] CD audit align: moved lead-only inline <img> row(s) after first substantive paragraph "
            "(client hero removed from body)."
        )
    return str(soup)


def _cd_simplify_heading_wrapper_spans(soup: BeautifulSoup) -> None:
    """Unwrap a sole decorative ``<span>`` inside ``h2``–``h6`` (common in GDoc exports) toward corpus ``h2>#text``."""
    for hx in soup.find_all(["h2", "h3", "h4", "h5", "h6"]):
        elems = [c for c in hx.children if getattr(c, "name", None)]
        if len(elems) != 1 or elems[0].name != "span":
            continue
        sp = elems[0]
        if sp.find(["a", "img"]):
            continue
        sp.unwrap()


def _strip_audit_style_number_prefix_from_h2(text: str) -> str:
    """
    Audit-dominant heading style on Cultural Daily is non-numbered H2.
    Treat a leading ordinal marker as formatting (not content), e.g.:
    ``1. Gut Health`` -> ``Gut Health``.
    """
    t = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"^\s*\d+\s*[\.\)\-:]\s+", "", t).strip()


def load_audit_format_profile() -> dict:
    """
    Data-driven formatting thresholds built from the Our Friends corpus
    (``scripts/build_audit_format_profile.py`` → ``data/audit_format_profile.json``).
    Optional embed: ``our_friends_audit.json`` may include ``html_format_profile``.
    """
    if AUDIT_FORMAT_PROFILE_JSON.is_file():
        try:
            return json.loads(AUDIT_FORMAT_PROFILE_JSON.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[warn] Could not read {AUDIT_FORMAT_PROFILE_JSON}: {e}")
    if OUR_FRIENDS_AUDIT_JSON.is_file():
        try:
            blob = json.loads(OUR_FRIENDS_AUDIT_JSON.read_text(encoding="utf-8"))
            return blob.get("html_format_profile") or {}
        except Exception as e:
            print(f"[warn] Could not read embedded html_format_profile: {e}")
    return {}


def format_to_audit_standard(html: str, *, site: dict) -> str:
    """
    Transform body HTML **structure** toward patterns in ``data/audit_format_profile.json``
    (Our Friends corpus). Does not change words, facts, or link URLs.

    Steps (CD only):

    1. Strip leading ``1.`` / ``2)`` style markers from ``<h2>`` when the profile says the corpus
       favors plain H2s (``thresholds.strip_h2_leading_ordinals``).
    2. Unwrap a sole decorative ``<span>`` inside ``<h2>``–``<h6>`` (GDoc export noise).
    3. ``normalize_cd_body_vertical_spacing`` — empty spacer ``<p>`` removal + newline collapse
       (aligns with ``collapse_runs_of_newlines_ge_3`` / ``max_serialized_newline_run`` in the profile).

    Section titles that Google exports as styled ``<p>`` are promoted to ``<h2>`` earlier in the
    pipeline via :func:`cd_promote_gdoc_heading_paragraphs` (same pass as audit alignment).
    """
    if not (html or "").strip():
        return html
    if site.get("key") != "cd":
        return html
    prof = load_audit_format_profile()
    th = (prof.get("thresholds") or {}) if isinstance(prof, dict) else {}
    strip_ord = th.get("strip_h2_leading_ordinals", True)

    soup = BeautifulSoup(html, "html.parser")
    if strip_ord:
        for h2 in soup.find_all("h2"):
            tx = h2.get_text(" ", strip=True)
            if not tx:
                continue
            stripped = _strip_audit_style_number_prefix_from_h2(tx)
            if stripped == tx:
                continue
            if h2.string is not None:
                h2.string.replace_with(stripped)
            else:
                h2.clear()
                h2.append(stripped)
    _cd_simplify_heading_wrapper_spans(soup)
    return normalize_cd_body_vertical_spacing(str(soup))


def apply_audit_formatting_patterns(html: str, *, site: dict) -> str:
    """Back-compat alias for :func:`format_to_audit_standard`."""
    return format_to_audit_standard(html, site=site)


def _p_contains_only_img(par: Any) -> bool:
    if par is None or getattr(par, "name", "") != "p":
        return False
    for child in par.children:
        if isinstance(child, NavigableString):
            if str(child).strip():
                return False
        elif getattr(child, "name", "") != "img":
            return False
    return par.find("img") is not None


def _cd_unwrap_span_wrappers_around_figure(fig) -> None:
    """Google Docs often wraps ``<img>`` in ``<span>``; unwrap so the block figure can center."""
    for _ in range(12):
        par = fig.parent
        if par is None or getattr(par, "name", "") != "span":
            break
        texts = [str(c).strip() for c in par.children if isinstance(c, NavigableString)]
        if any(texts):
            break
        elems = [c for c in par.children if getattr(c, "name", None)]
        if len(elems) == 1 and elems[0] is fig:
            par.unwrap()
        else:
            break


def _cd_center_paragraph_parent_of_figure(fig) -> None:
    """If the figure sits in a ``<p>`` (invalid but common from exports), center that paragraph."""
    p = fig.parent
    if p is None or getattr(p, "name", "") != "p":
        return
    p["style"] = _merge_css_style(p.get("style"), "text-align:center")
    cls = p.get("class")
    if cls is None:
        p["class"] = ["aligncenter"]
    elif isinstance(cls, list):
        if "aligncenter" not in cls:
            cls.append("aligncenter")
    else:
        parts = str(cls).split()
        if "aligncenter" not in parts:
            parts.append("aligncenter")
        p["class"] = parts


def _cd_merge_wp_image_class(img, media_id: int) -> None:
    """WordPress ties attachments to ``<img class=\"wp-image-{id}\">`` so the editor shows alignment + alt."""
    token = f"wp-image-{int(media_id)}"
    cls = img.get("class") or []
    if isinstance(cls, str):
        cls = [c for c in cls.split() if c]
    cls = [c for c in cls if not (isinstance(c, str) and c.startswith("wp-image-"))]
    if token not in cls:
        cls.append(token)
    img["class"] = cls


def _cd_apply_figure_center_styles(fig, img) -> None:
    cls = fig.get("class") or []
    if isinstance(cls, str):
        cls = [c for c in cls.split() if c]
    for token in ("wp-block-image", "aligncenter"):
        if token not in cls:
            cls.append(token)
    fig["class"] = cls
    fig["align"] = "center"
    fig["style"] = _merge_css_style(
        fig.get("style"),
        "margin-left:auto;margin-right:auto;text-align:center;max-width:100%",
    )
    img["style"] = _merge_css_style(
        img.get("style"),
        "display:block;margin-left:auto;margin-right:auto;max-width:100%;height:auto",
    )
    icls = img.get("class") or []
    if isinstance(icls, str):
        icls = [c for c in icls.split() if c]
    for token in ("aligncenter", "size-full"):
        if token not in icls:
            icls.append(token)
    img["class"] = icls
    img["align"] = "center"


def cd_format_body_inline_images(html: str, *, post_title: str = "", site: dict) -> str:
    """
    Center inline images (WordPress-friendly ``aligncenter`` + margins) and unwrap GDoc ``<span>``
    wrappers; ensure alt is a photo description (never the article title); place a following
    ``Photo:`` credit in a centered caption paragraph when present.
    """
    soup = BeautifulSoup(html, "html.parser")
    imgs = [
        img
        for img in list(soup.find_all("img"))
        if (img.get("src") or "").strip().lower().startswith("http")
    ]
    for slot, img in enumerate(imgs, start=1):
        alt0 = (img.get("alt") or img.get("title") or "").strip()
        img["alt"] = _cd_inline_alt_for_img(alt0, post_title, slot=slot)
        img["title"] = cd_insert_media_title(site, slot)
        cap_txt = ""
        nxt = img.find_next("p")
        if nxt and nxt.get_text(" ", strip=True).lower().startswith("photo:"):
            cap_txt = nxt.get_text(" ", strip=True)
            nxt.decompose()
        parent = img.parent
        # Reuse any existing <figure> (with or without align=) — avoids nesting a second <figure>.
        if parent and getattr(parent, "name", "") == "figure":
            fig = parent
        elif parent and _p_contains_only_img(parent):
            fig = soup.new_tag("figure", attrs={"align": "center"})
            parent.replace_with(fig)
            fig.append(img)
        else:
            fig = soup.new_tag("figure", attrs={"align": "center"})
            img.replace_with(fig)
            fig.append(img)
        _cd_apply_figure_center_styles(fig, img)
        _cd_unwrap_span_wrappers_around_figure(fig)
        _cd_center_paragraph_parent_of_figure(fig)
        if cap_txt:
            cap_p = soup.new_tag(
                "p",
                attrs={"style": "display:block;margin:0 auto;text-align:center"},
            )
            em = soup.new_tag("em")
            em.append(cap_txt)
            cap_p.append(em)
            fig.insert_after(cap_p)
    return str(soup)


def build_cd_aioseo_seo_title(full_h1: str, planner_hint: str) -> str:
    """
    AIOSEO title: max 60 chars, never truncate mid-word; optional `` | Cultural Daily`` when it still fits.
    """
    lim = 60
    hint = (planner_hint or "").strip()
    if hint and len(hint) <= lim:
        base = hint
    else:
        t = unicodedata.normalize("NFKC", (full_h1 or "").strip())
        if len(t) <= lim:
            base = t
        else:
            chunk = t[:lim]
            if chunk[-1].isspace():
                base = chunk.strip()
            else:
                sp = chunk.rfind(" ")
                base = chunk[:sp].rstrip() if sp >= 12 else re.sub(r"\W+$", "", chunk).strip()
            if not base:
                base = t[: lim - 1].rstrip() + "…"
    suf = CD_SEO_TITLE_SUFFIX
    if "cultural daily" not in base.lower() and len(base) + len(suf) <= lim:
        base = (base + suf).strip()
    if len(base) > lim:
        base = base[:lim]
        sp = base.rfind(" ")
        if sp >= 10:
            base = base[:sp].rstrip()
    return base[:lim].strip()


def ensure_meta_description_length(meta: str, filler_plain: str) -> str:
    """Meta description between META_DESCRIPTION_MIN and META_DESCRIPTION_MAX characters."""
    m = unicodedata.normalize("NFKC", (meta or "").strip())
    if len(m) > META_DESCRIPTION_MAX:
        m = m[: META_DESCRIPTION_MAX - 3].rsplit(" ", 1)[0] + "..."
    if len(m) >= META_DESCRIPTION_MIN:
        return m[:META_DESCRIPTION_MAX]
    fill = re.sub(r"\s+", " ", filler_plain or "").strip()
    if m and fill:
        room = META_DESCRIPTION_MIN - len(m) - 1
        if room > 0:
            extra = fill[: room + 120].rsplit(" ", 1)[0]
            if len(extra) > room:
                extra = extra[:room].rsplit(" ", 1)[0]
            if extra:
                m = (m + " " + extra).strip()
    elif fill:
        m = fill[:META_DESCRIPTION_MAX]
        if len(m) > META_DESCRIPTION_MAX:
            m = m[: META_DESCRIPTION_MAX - 3].rsplit(" ", 1)[0] + "..."
    pad = " Sponsored arts and culture coverage on Cultural Daily."
    while len(m) < META_DESCRIPTION_MIN:
        m = (m + pad).strip()
        if len(m) > META_DESCRIPTION_MAX:
            m = m[:META_DESCRIPTION_MAX]
            break
    return m[:META_DESCRIPTION_MAX]


def hero_social_alt_for_cd(
    *,
    planner_alt: str,
    post_title: str,
    used_client_hero: bool,
    hero_pexels_query: str,
    topic_slug: str,
) -> str:
    """Hero + social alt: simple photo description — never the article H1 (CRITICAL_RULES)."""
    tnorm = _norm_title_text(post_title)
    pa = unicodedata.normalize("NFKC", (planner_alt or "").strip())
    if pa and _norm_title_text(pa) != tnorm and 8 <= len(pa) <= 180:
        return pa[:180]
    slug_phrase = topic_slug.replace("-", " ").strip() or "this story"
    if used_client_hero:
        return f"Photograph supplied by the advertiser showing {slug_phrase}"
    q = (hero_pexels_query or slug_phrase).strip()[:80]
    return f"Wide banner photograph for article: {q}"


def _pil_image_from_src(src: str) -> Image.Image:
    src = (src or "").strip()
    if src.startswith("data:"):
        import base64

        _, b64 = src.split(",", 1)
        raw = base64.b64decode(b64)
        return Image.open(io.BytesIO(raw)).convert("RGB")
    r = requests.get(
        src,
        timeout=120,
        headers={"User-Agent": "ScoutmonkeysPipeline/1.0"},
    )
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def build_resized_pair_from_pil(site: dict, img: Image.Image) -> Tuple[Image.Image, Image.Image]:
    hero = _resize_cover_exact_floor(img, site["hero_w"], site["hero_h"])
    social = _resize_cover_exact_floor(img, site["social_w"], site["social_h"])
    return hero, social


def attempt_image_provenance(img: Image.Image) -> Tuple[Optional[str], str, List[str]]:
    """CRITICAL_RULES §4 — hook for reverse search / EXIF metadata."""
    _ = img
    # No URL and empty label → blank captions / no placeholder (CRITICAL_RULES).
    return None, "", ["reverse_image_lookup_not_implemented"]


def compact_focus_keyword(raw: str, *, max_words: int = 4, max_len: int = 48) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    words = raw.split()
    if len(words) > max_words:
        raw = " ".join(words[:max_words])
    return raw[:max_len].rstrip(" -–—")


_CD_FOCUS_STOPWORDS = frozenset(
    """
    a an the and or but if as at by for from in into of on to with without is are was were
    be been being have has had do does did will would could should may might must can
    this that these those it its they them their we our you your i me my he him his she her
    what which who whom whose when where why how all each every both few more most some
    such no nor not only own same so than too very just also there here even ever still
    already then now once again about above after before between through during under over
    out up down off within across along though although whether another other any many much
    say said says get got go going went come came make made take took see saw know knew
    think thought want wanted one two first last next new old long big small high low let
    lets via per vs
    """.split()
)


def cd_title_focus_keyword_candidates(title: str) -> List[str]:
    """
    Ordered keyphrase candidates from the post title: **single words first** (audit / AIOSEO
    favor short focus keys), then adjacent two-word spans. Skips obvious stopwords.
    """
    t = unicodedata.normalize("NFKC", (title or "")).strip()
    if not t:
        return []
    tl = t.lower()
    words = re.findall(r"[a-z0-9]+(?:'[a-z]+)?", tl)
    if not words:
        return []
    out: List[str] = []
    seen: set[str] = set()

    def push(w: str) -> None:
        w = w.strip().lower()
        if not w or w in seen or len(w) < 2:
            return
        seen.add(w)
        out.append(w)

    for w in words:
        if w in _CD_FOCUS_STOPWORDS or len(w) < 3:
            continue
        push(w)
    for w in words:
        if len(w) != 2 or not w.isalnum() or w in seen:
            continue
        push(w)
    for i in range(len(words) - 1):
        a, b = words[i], words[i + 1]
        if len(a) < 2 or len(b) < 2:
            continue
        if a in _CD_FOCUS_STOPWORDS or b in _CD_FOCUS_STOPWORDS:
            continue
        push(f"{a} {b}")
    return out


def focus_keyword_content_score(
    keyphrase: str, haystack: str, *, title: str = ""
) -> float:
    """
    Lightweight relevance score (0–100) of ``keyphrase`` against article text, with a **title
    match bonus** (AIOSEO strongly weights the keyphrase appearing in the H1).
    Calibrated so title-derived 1–2 word keys used by :func:`refine_focus_keyword_for_content`
    normally clear the CD QA bar (≥82).
    """
    k = (keyphrase or "").strip().lower()
    h = (haystack or "").lower()
    ti = unicodedata.normalize("NFKC", (title or "")).strip().lower()
    if not k or not h:
        return 0.0
    if k in h:
        sc = min(100.0, 72.0 + min(28.0, float(h.count(k)) * 6.0))
    else:
        parts = [w for w in k.split() if len(w) > 1]
        if not parts:
            return 0.0
        hits = sum(1 for w in parts if w in h)
        sc = min(99.0, (hits / len(parts)) * 88.0)
    if ti and k in ti:
        if k in h:
            sc = max(
                sc,
                min(
                    100.0,
                    82.0 + min(18.0, max(0.0, float(h.count(k)) - 1.0) * 6.0),
                ),
            )
        elif " " not in k:
            sc = max(sc, 77.0)
        else:
            sc = max(sc, min(100.0, 80.0 + min(20.0, float(h.count(k)) * 8.0)))
    return sc


def _cd_pick_focus_keyword_by_score(
    candidates: List[str],
    hay: str,
    title: str,
    *,
    target: float = 82.0,
) -> str:
    """Prefer the shortest (usually 1-word) candidate that meets ``target``; else best score."""
    ti = unicodedata.normalize("NFKC", (title or "")).strip()
    seen: set[str] = set()
    uniq: List[str] = []
    for c in candidates:
        c2 = compact_focus_keyword(c, max_words=2, max_len=36)
        if not c2 or c2 in seen:
            continue
        seen.add(c2)
        uniq.append(c2)
    if not uniq:
        return ""
    scored: List[Tuple[float, int, int, str]] = []
    for i, k in enumerate(uniq):
        sc = focus_keyword_content_score(k, hay, title=ti)
        scored.append((sc, len(k.split()), i, k))
    qual = [t for t in scored if t[0] >= target - 0.001]
    if qual:
        qual.sort(key=lambda t: (t[1], -t[0], t[2]))
        return qual[0][3]
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    return scored[0][3]


def refine_focus_keyword_for_content(
    focus: str,
    *,
    body: str,
    doc_html: str,
    title: str,
    topic_slug: str,
) -> str:
    """
    **1 word from the title when possible** (sometimes 2). Picks the shortest phrase that scores
    ≥82 against body + doc + title so AIOSEO is not dragged down by a long planner keyphrase.
    """
    hay = f"{body}\n{doc_html}\n{title}"
    ti = unicodedata.normalize("NFKC", (title or "")).strip()
    candidates: List[str] = []
    candidates.extend(cd_title_focus_keyword_candidates(ti))
    base = compact_focus_keyword(focus, max_words=2, max_len=36)
    if not base:
        base = compact_focus_keyword(topic_slug.replace("-", " "), max_words=2, max_len=36)
    if base:
        candidates.append(base)
        ws = base.split()
        for n in range(min(len(ws), 2), 0, -1):
            candidates.append(" ".join(ws[:n]))
        if ws:
            candidates.append(ws[0])
    candidates.append(compact_focus_keyword(topic_slug.replace("-", " "), max_words=2, max_len=36))
    chosen = _cd_pick_focus_keyword_by_score(candidates, hay, ti, target=82.0)
    if chosen:
        sc = focus_keyword_content_score(chosen, hay, title=ti)
        if sc >= 81.99:
            return chosen

    seen: set[str] = {chosen} if chosen else set()
    for w in topic_slug.replace("-", " ").split():
        t = compact_focus_keyword(w, max_words=1, max_len=24)
        if not t or t in seen:
            continue
        seen.add(t)
        sc = focus_keyword_content_score(t, hay, title=ti)
        if sc >= 82.0:
            return t
    for w in ("culture", "food", "health", "film", "music", "art", "books"):
        if w in seen:
            continue
        sc = focus_keyword_content_score(w, hay, title=ti)
        if sc >= 82.0:
            return w
    for w in re.findall(r"[a-z][a-z'-]{3,}", (topic_slug + " " + ti).lower()):
        if w in seen or len(w) < 4:
            continue
        seen.add(w)
        sc = focus_keyword_content_score(w, hay, title=ti)
        if sc >= 82.0:
            return w
    return chosen or compact_focus_keyword(topic_slug.replace("-", " "), max_words=2, max_len=36)


def derive_meta_from_gdoc_first_paragraph(ghtml: str, max_len: int = 160) -> str:
    soup = BeautifulSoup(ghtml, "html.parser")
    for p in soup.find_all("p"):
        t = p.get_text(" ", strip=True)
        if len(t) >= 40:
            if len(t) <= max_len:
                return t
            cut = t[: max_len - 3].rsplit(" ", 1)[0]
            return cut + "..."
    return ""


def client_photo_citation_html(credit_url: Optional[str], credit_label: str) -> str:
    """
    HTML citation for client-sourced hero. When there is no credit URL and no label,
    returns empty string (no placeholder paragraph — CRITICAL_RULES).
    """
    label = (credit_label or "").strip()
    if not credit_url and not label:
        return ""
    if credit_url:
        inner = f"Photo: {label}" if label else "Photo"
        return (
            f'<p><em><a href="{credit_url}" target="_blank" rel="nofollow noopener">'
            f"{inner}</a></em></p>"
        )
    inner = f"Photo: {label}" if label else "Photo"
    return f"<p><em>{inner}</em></p>"


CD_SPONSOR_CATEGORY_ALLOWLIST_JSON = REPO_ROOT / "data" / "cd_sponsor_category_allowlist.json"
SPONSORED_LAST_YEAR_AUDIT_JSON = REPO_ROOT / "data" / "sponsored_last_year_audit.json"

# Planner hints treated as “generic” — machine lane detection may override (see ``infer_cd_sponsor_category_hint``).
_CD_GENERIC_CATEGORY_HINTS = frozenset(
    {
        "",
        "check this out",
        "check-this-out",
        "check this out ",
        "culture",
        "general",
        "arts",
        "arts and culture",
        "art",
    }
)

# Slugs we may try even when absent from a stale audit export (WordPress must still define them).
_CD_SPONSOR_LANE_SLUGS = frozenset({"casino", "grey-niche", "crypto", "sports"})


def cd_sponsor_category_forbidden(slug: str, name: str) -> bool:
    """Hard blocks for Our Friends CD category assignment (slug + display name)."""
    slug_l = (slug or "").lower()
    name_l = (name or "").lower()
    if slug_l == "sponsored":
        return True
    if "featured" in slug_l and "story" in slug_l:
        return True
    if re.search(r"featured[-\s_]?story", slug_l):
        return True
    if "featured" in name_l and "story" in name_l:
        return True
    return False


def _slugify_category_hint(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t)
    return t.strip("-")


def _load_cd_sponsor_category_allowlist() -> Optional[set[str]]:
    p = CD_SPONSOR_CATEGORY_ALLOWLIST_JSON
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    slugs = data.get("slugs")
    if not isinstance(slugs, list):
        return None
    out = {str(s).strip().lower() for s in slugs if str(s).strip()}
    return out or None


def _effective_cd_category_allowlist() -> Optional[set[str]]:
    """
    Union of explicit ``cd_sponsor_category_allowlist.json`` and non-forbidden slugs from
    ``sponsored_last_year_audit.json`` (when those files exist). Always includes ``check-this-out``.

    Returns ``None`` when **no** audit/allowlist file contributed data — then any non-forbidden
    slug from hints/topic may be tried against WordPress.
    """
    merged: set[str] = set()
    had_source = False
    expl = _load_cd_sponsor_category_allowlist()
    if expl:
        merged |= expl
        had_source = True
    p = SPONSORED_LAST_YEAR_AUDIT_JSON
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            rows = data.get("category_slug_counts") or []
            for row in rows:
                if row.get("cd_sponsor_forbidden"):
                    continue
                s = (row.get("slug") or "").strip().lower()
                if s:
                    merged.add(s)
            if rows:
                had_source = True
        except (OSError, json.JSONDecodeError) as e:
            print(f"[warn] Could not read category_slug_counts from {p}: {e}")
    merged.add("check-this-out")
    merged |= _CD_SPONSOR_LANE_SLUGS
    if not had_source and not expl:
        return None
    return merged


def infer_cd_sponsor_category_hint(
    planner_hint: str,
    *,
    topic_slug: str,
    title: str,
    plaintext_excerpt: str,
) -> str:
    """
    When the planner leaves a **generic** category hint, infer a WordPress-facing lane from
    ``topic_slug``, ``title``, and a plaintext Doc excerpt (no LLM). Examples: gambling / slots /
    loot boxes → ``casino``; grey + niche → ``grey niche``. Specific planner hints are kept as-is.
    """
    raw = (planner_hint or "").strip()
    slug_pl = _slugify_category_hint(raw)
    if raw and slug_pl not in _CD_GENERIC_CATEGORY_HINTS:
        return raw
    blob = f"{topic_slug} {title} {plaintext_excerpt}".lower()
    if "grey" in blob and "niche" in blob:
        return "grey niche"
    if any(
        k in blob
        for k in (
            "casino",
            "slot machine",
            "slot-m",
            "loot box",
            "lootbox",
            "loot-box",
            "sportsbook",
            "sports book",
            "blackjack",
            "roulette",
            "jackpot",
            "gambling",
            "betting",
            "wager",
            "craps",
            "poker",
        )
    ):
        return "casino"
    if any(k in blob for k in ("crypto", "bitcoin", "blockchain", "defi", "nft")):
        return "crypto"
    if any(k in blob for k in ("sports betting", "sports-betting", "bookmaker", "odds boost")):
        return "sports"
    if raw:
        return raw
    return "Check This Out"


def _cd_category_slug_candidates(category_hint: str, topic_slug: str) -> List[str]:
    """Ordered slug candidates: hint first, then topic tokens (gambling-like parts before generic words)."""
    out: List[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        s = _slugify_category_hint(s)
        if len(s) < 2 or s in seen:
            return
        seen.add(s)
        out.append(s)

    def _topic_part_rank(part: str) -> tuple[int, int]:
        p = part.lower()
        if any(k in p for k in ("gambl", "slot", "casino", "bet", "loot", "wager", "poker", "jackpot")):
            return (0, -len(p))
        if any(k in p for k in ("grey", "niche", "sport", "crypto", "book")):
            return (1, -len(p))
        return (2, -len(p))

    add(category_hint or "")
    ts = re.sub(r"[^a-z0-9-]+", "-", (topic_slug or "").lower()).strip("-")
    parts = [p for p in ts.split("-") if len(p) >= 4]
    parts.sort(key=_topic_part_rank)
    for part in parts:
        add(part)
    if ts:
        add(ts)
    return out


def resolve_wp_category_id_by_slug(
    site: dict, slug: str, *, for_cd_sponsor: bool
) -> Optional[int]:
    wp, auth = wp_auth(site)
    slug = (slug or "").strip().lower()
    if not slug:
        return None
    r = requests.get(
        f"{wp}/wp-json/wp/v2/categories",
        auth=auth,
        params={"slug": slug, "per_page": 5},
        timeout=30,
    )
    r.raise_for_status()
    for row in r.json():
        rs = (row.get("slug") or "").lower()
        rn = row.get("name") or ""
        if rs != slug:
            continue
        if for_cd_sponsor and cd_sponsor_category_forbidden(rs, rn):
            return None
        return int(row["id"])
    return None


def resolve_cd_sponsored_category(
    site: dict,
    *,
    category_hint: str,
    topic_slug: str,
    title: str = "",
) -> Tuple[int, str]:
    """
    Cultural Daily Our Friends: pick the first **resolving** category slug from the candidate list
    (refined ``category_hint`` + ``topic_slug`` tokens), gated by :func:`_effective_cd_category_allowlist`
    when audit / allowlist files exist. Falls back to **Check This Out**.

    ``CD_SPONSOR_CATEGORY_SLUG`` forces a single slug (must resolve and not be forbidden).
    ``title`` is accepted for API symmetry with :func:`infer_cd_sponsor_category_hint`; candidates
    are built from ``category_hint`` and ``topic_slug``.
    """
    _ = title
    env_raw = (os.environ.get("CD_SPONSOR_CATEGORY_SLUG") or "").strip()
    if env_raw:
        es = _slugify_category_hint(env_raw)
        cid = resolve_wp_category_id_by_slug(site, es, for_cd_sponsor=True)
        if cid is None:
            raise RuntimeError(
                f"CD_SPONSOR_CATEGORY_SLUG={env_raw!r} does not resolve to a WordPress category "
                "or is forbidden (Sponsored / Featured Story)."
            )
        return cid, es

    allow = _effective_cd_category_allowlist()
    ordered = _cd_category_slug_candidates(category_hint, topic_slug)
    for slug in ordered:
        gated = allow is None or slug in allow or slug == "check-this-out"
        if not gated:
            continue
        cid = resolve_wp_category_id_by_slug(site, slug, for_cd_sponsor=True)
        if cid is not None:
            return cid, slug

    cto = resolve_check_this_out_category(site)
    raw = (category_hint or "").strip()
    hs = _slugify_category_hint(raw)
    if hs and hs != "check-this-out":
        return cto, f"check-this-out (fallback; tried {ordered[:8]!r})"
    return cto, "check-this-out"


def assert_cd_social_attachment_stored_dimensions(
    site: dict, media: dict, *, context: str
) -> None:
    """Fail fast if WordPress did not keep the social JPEG at CD pixel dimensions."""
    if site.get("key") != "cd":
        return
    md = media.get("media_details") or {}
    sw = int(md.get("width") or 0)
    sh = int(md.get("height") or 0)
    ew, eh = int(site["social_w"]), int(site["social_h"])
    if (sw, sh) != (ew, eh):
        relax = (os.environ.get("CD_RELAX_SOCIAL_WP_PIXEL_ASSERT") or "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if relax:
            print(
                f"[warn] WordPress stored social as {sw}×{sh}, expected {ew}×{eh} ({context}). "
                "Continuing because CD_RELAX_SOCIAL_WP_PIXEL_ASSERT=1 — fix host scaling when you can."
            )
            return
        raise RuntimeError(
            f"CD social image must be stored as {ew}×{eh}px in WordPress (CRITICAL_RULES §11). "
            f"After upload, REST reports {sw}×{sh} ({context}). "
            "The file was uploaded at the correct size in the pipeline — if dimensions shrink here, "
            "the site is applying \"big image\" downscaling or another image plugin/CDN rule. "
            "Raise WordPress `big_image_size_threshold` (e.g. ≥2560) or disable that scaling so "
            f"{ew}×{eh} attachments are stored unchanged, then re-run. "
            "Or set CD_RELAX_SOCIAL_WP_PIXEL_ASSERT=1 once to save a draft despite scaled pixels."
        )


def resolve_check_this_out_category(site: dict) -> int:
    wp, auth = wp_auth(site)
    for slug in ("check-this-out", "check-this-out-1", "check-this-out-2"):
        r = requests.get(
            f"{wp}/wp-json/wp/v2/categories",
            auth=auth,
            params={"slug": slug},
            timeout=30,
        )
        r.raise_for_status()
        rows = r.json()
        if rows:
            return int(rows[0]["id"])
    r = requests.get(
        f"{wp}/wp-json/wp/v2/categories",
        auth=auth,
        params={"search": "Check This Out", "per_page": 30},
        timeout=30,
    )
    r.raise_for_status()
    for row in r.json():
        name = (row.get("name") or "").lower()
        slug = (row.get("slug") or "").lower()
        if "check" in name and "out" in name:
            return int(row["id"])
        if "check" in slug and "out" in slug:
            return int(row["id"])
    raise RuntimeError(
        "CRITICAL_RULES: WordPress category 'Check This Out' not found — create it or fix the slug."
    )


def photographer_meta(photo: dict) -> Tuple[str, str, str]:
    name = (photo.get("photographer") or "").strip() or "Photographer"
    profile = (photo.get("photographer_url") or "").strip()
    page = (photo.get("url") or "").strip()
    if not profile:
        profile = "https://www.pexels.com/"
    return name, profile, page


# ---------------------------------------------------------------------------
# WordPress
# ---------------------------------------------------------------------------


def wp_auth(site: dict) -> Tuple[str, Tuple[str, str]]:
    return site["wp_url"], (site["wp_user"], site["wp_pass"])


def wp_upload_jpeg(
    site: dict, image: Image.Image, filename: str, title: str, alt: str, caption: str
) -> dict:
    wp, auth = wp_auth(site)
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=92)
    buf.seek(0)
    files = {"file": (filename, buf, "image/jpeg")}
    r = requests.post(f"{wp}/wp-json/wp/v2/media", auth=auth, files=files, timeout=120)
    r.raise_for_status()
    media = r.json()
    mid = media["id"]
    update = {
        "title": title,
        "alt_text": alt,
        "caption": caption,
    }
    r2 = requests.post(
        f"{wp}/wp-json/wp/v2/media/{mid}",
        auth=auth,
        json=update,
        timeout=60,
    )
    r2.raise_for_status()
    return r2.json()


def cd_resolve_media_id_by_title(site: dict, title: str) -> Optional[int]:
    """Find a media attachment whose title matches exactly (REST ``search`` + local filter)."""
    wp, auth = wp_auth(site)
    t = html_module.unescape((title or "").strip())
    if not t:
        return None
    r = requests.get(
        f"{wp}/wp-json/wp/v2/media",
        auth=auth,
        params={"search": t, "per_page": 50, "orderby": "date", "order": "desc"},
        timeout=30,
    )
    r.raise_for_status()
    want = t.lower()
    for row in r.json():
        tit = row.get("title") or {}
        raw = (tit.get("raw") or tit.get("rendered") or "").strip()
        raw = html_module.unescape(re.sub(r"<[^>]+>", "", raw))
        if raw.lower() == want:
            return int(row["id"])
    return None


def cd_sync_inline_attachment_alts_from_body(site: dict, body_html: str) -> None:
    """
    Copy ``<img alt=\"…\">`` and ``title=\"…\"`` (``CD-InsertN``) onto WordPress attachment fields for
    each ``wp-image-{id}`` class so the Classic editor **Image details** modal shows alt + title.
    """
    if site.get("key") != "cd" or not (body_html or "").strip():
        return
    soup = BeautifulSoup(body_html, "html.parser")
    wp, auth = wp_auth(site)
    seen: set[int] = set()
    for img in soup.find_all("img"):
        cls = img.get("class") or []
        if isinstance(cls, str):
            cls = cls.split()
        mid: Optional[int] = None
        for c in cls:
            if isinstance(c, str) and c.startswith("wp-image-"):
                try:
                    mid = int(c.replace("wp-image-", "", 1))
                except ValueError:
                    mid = None
                break
        if mid is None:
            continue
        alt = (img.get("alt") or "").strip()
        tit = (img.get("title") or "").strip()
        if mid in seen:
            continue
        if not alt and not tit:
            continue
        seen.add(mid)
        payload: Dict[str, str] = {}
        if alt:
            payload["alt_text"] = alt
        if tit:
            payload["title"] = tit
        if not payload:
            continue
        r = requests.post(
            f"{wp}/wp-json/wp/v2/media/{mid}",
            auth=auth,
            json=payload,
            timeout=30,
        )
        if not r.ok:
            print(f"[warn] media alt/title sync failed id={mid}: {r.status_code} {r.text[:160]}")


def _basename_media_path(url: str) -> str:
    return (urlparse(url or "").path or "").rsplit("/", 1)[-1].lower()


def _normalize_wp_media_basename(url: str) -> str:
    """Strip common WordPress size suffixes so OG URL can be compared to ``source_url``."""
    b = _basename_media_path(url)
    b = re.sub(r"-scaled(?=\.[a-z]+$)", "", b, flags=re.I)
    b = re.sub(r"-\d+x\d+(?=\.[a-z]+$)", "", b, flags=re.I)
    return b


def cd_insert_media_title(site: dict, slot: int) -> str:
    """WordPress media title + basename stem for inline inserts: ``CD-Insert1``, ``CD-Insert2``, …"""
    pfx = (site.get("prefix") or "CD").strip() or "CD"
    return f"{pfx}-Insert{int(slot)}"


def _cd_insert_src_matches_slot_title(site: dict, src: str, slot: int) -> bool:
    """True when ``src`` basename already matches this pipeline's insert naming (current or legacy)."""
    if slot < 1:
        return False
    base = _basename_media_path(src).lower()
    cur = cd_insert_media_title(site, slot).lower()
    if base.startswith(cur + ".jp"):
        return True
    pfx = (site.get("prefix") or "CD").strip().lower()
    # Legacy: ``{prefix}-{topic}-insert-{n}.jpg``
    m = re.search(r"-insert-(\d+)\.jpe?g$", base, re.I)
    if m and pfx and base.startswith(pfx + "-"):
        try:
            leg_slot = int(m.group(1))
        except ValueError:
            return False
        return leg_slot == slot
    return False


def cd_reupload_inline_body_images(
    site: dict,
    body_html: str,
    *,
    topic_slug: str,
    post_title: str,
    hero_src_to_skip: str = "",
) -> str:
    """
    Upload each non-hero inline ``<img>`` to WordPress as ``{prefix}-Insert1``, ``{prefix}-Insert2``, …
    (filename + attachment title). Skips URLs already named for that slot (current or legacy
    ``{prefix}-{topic}-insert-{n}`` basename).
    """
    if not (body_html or "").strip():
        return body_html
    _ = topic_slug  # retained for API compatibility; insert titles no longer embed the topic slug
    soup = BeautifulSoup(body_html, "html.parser")
    changed = False
    slot = 0
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        if not src.lower().startswith("http"):
            continue
        if hero_src_to_skip and _urls_loosely_same(src, hero_src_to_skip):
            continue
        slot += 1
        if _cd_insert_src_matches_slot_title(site, src, slot):
            title_m = cd_insert_media_title(site, slot)
            alt0 = (img.get("alt") or img.get("title") or "").strip()
            new_alt = _cd_inline_alt_for_img(alt0, post_title, slot=slot)
            if (img.get("alt") or "") != new_alt:
                img["alt"] = new_alt
                changed = True
            if (img.get("title") or "").strip() != title_m:
                img["title"] = title_m
                changed = True
            mid: Optional[int] = None
            cls = img.get("class") or []
            if isinstance(cls, str):
                cls = cls.split()
            for c in cls:
                if isinstance(c, str) and c.startswith("wp-image-"):
                    try:
                        mid = int(c.replace("wp-image-", "", 1))
                    except ValueError:
                        mid = None
                    break
            if mid is None:
                mid = cd_resolve_media_id_by_title(site, title_m)
            if mid is not None:
                _cd_merge_wp_image_class(img, mid)
                changed = True
            continue
        cap_txt = ""
        nxt = img.find_next("p")
        if nxt and nxt.get_text(" ", strip=True).lower().startswith("photo:"):
            cap_txt = nxt.get_text(" ", strip=True)
            nxt.decompose()
            changed = True
        alt0 = (img.get("alt") or img.get("title") or "").strip()
        alt_f = _cd_inline_alt_for_img(alt0, post_title, slot=slot)
        try:
            pil = _pil_image_from_src(src).convert("RGB")
        except Exception as e:
            print(f"[warn] inline image upload skipped ({src[:90]}…): {e}")
            continue
        title_m = cd_insert_media_title(site, slot)
        fn = f"{title_m}.jpg"
        try:
            m = wp_upload_jpeg(site, pil, fn, title_m, alt_f, cap_txt)
        except Exception as e:
            print(f"[warn] inline image WordPress upload failed ({fn}): {e}")
            continue
        nu = (m.get("source_url") or "").strip()
        if nu:
            img["src"] = nu
            img["alt"] = alt_f
            img["title"] = title_m
            mid_up = m.get("id")
            if mid_up is not None:
                _cd_merge_wp_image_class(img, int(mid_up))
            changed = True
    return str(soup) if changed else body_html


def resolve_default_category(site: dict, hint: str) -> int:
    wp, auth = wp_auth(site)
    for slug in ("our-friends", "friends", "sponsored"):
        r = requests.get(
            f"{wp}/wp-json/wp/v2/categories",
            auth=auth,
            params={"slug": slug},
            timeout=30,
        )
        r.raise_for_status()
        rows = r.json()
        if rows:
            return int(rows[0]["id"])
    r = requests.get(
        f"{wp}/wp-json/wp/v2/categories",
        auth=auth,
        params={"search": hint, "per_page": 20},
        timeout=30,
    )
    r.raise_for_status()
    rows = r.json()
    if rows:
        return int(rows[0]["id"])
    raise RuntimeError("Could not resolve a default WordPress category id")


def create_wp_draft(
    site: dict,
    title: str,
    content: str,
    seo: dict,
    category_id: int,
    hero_id: int,
    social_url: str,
    excerpt: str,
) -> dict:
    wp, auth = wp_auth(site)
    payload = {
        "title": title,
        "content": content,
        "status": "draft",
        "featured_media": hero_id,
        "categories": [category_id],
        "excerpt": excerpt,
        "author": site["author_id"],
    }
    r = requests.post(f"{wp}/wp-json/wp/v2/posts", auth=auth, json=payload, timeout=120)
    r.raise_for_status()
    post = r.json()
    return post


def push_aioseo_and_cdseo(
    site: dict,
    post_id: int,
    seo: dict,
    og_custom_url: str,
    *,
    seo_title_max: Optional[int] = None,
) -> None:
    wp, auth = wp_auth(site)
    st_clip = int(seo_title_max) if seo_title_max is not None else int(site["seo_title_max"])
    # 1) AIOSEO custom endpoint (Cultural Daily)
    st = (seo.get("seo_title") or "").strip()
    md = (seo.get("meta_description") or "").strip()
    body = {
        "postId": post_id,
        "post_id": post_id,
        "title": st,
        "description": md,
        "og_title": st,
        "og_description": md,
        "twitter_title": st,
        "twitter_description": md,
        "og_image_type": "custom",
        "og_image_custom_url": og_custom_url,
        "og_image_custom": True,
        "twitter_use_og": True,
        "twitter_image_custom_url": og_custom_url,
        "keyphrases": json.dumps(
            {"focus": {"keyphrase": seo.get("focus_keyword") or ""}},
            ensure_ascii=False,
        ),
    }
    # AIOSEO expects postId like GET ``…/post?postId=`` — JSON body alone returns "Post ID is missing" on some installs.
    r = requests.post(
        f"{wp}/wp-json/aioseo/v1/post",
        auth=auth,
        params={"postId": int(post_id)},
        json=body,
        timeout=60,
    )
    if not r.ok:
        r2 = requests.post(
            f"{wp}/wp-json/aioseo/v1/post/{int(post_id)}",
            auth=auth,
            json=body,
            timeout=60,
        )
        if r2.ok:
            r = r2
        else:
            print(f"[warn] aioseo/v1/post {r.status_code}: {r.text[:400]}")
            if r2.status_code != r.status_code:
                print(f"[warn] aioseo/v1/post/{post_id} fallback {r2.status_code}: {r2.text[:400]}")
    # 2) cd-seo plugin persists AIOSEO DB + OG (see wp-json /cd-seo/v1/update args)
    r_cd = requests.post(
        f"{wp}/wp-json/cd-seo/v1/update",
        auth=auth,
        json={
            "post_id": post_id,
            "seo_title": (seo.get("seo_title") or "")[:st_clip],
            "meta_description": (seo.get("meta_description") or "")[:160],
            "focus_keyphrase": (seo.get("focus_keyword") or "")[:191],
            "og_image_url": og_custom_url,
        },
        timeout=60,
    )
    if not r_cd.ok:
        print(f"[warn] cd-seo/v1/update {r_cd.status_code}: {r_cd.text[:400]}")


def find_social_attachment_by_title(site: dict, topic_slug: str, hero_id: int) -> Optional[int]:
    """
    Resolve the ``{prefix}-{topic}-social`` media row (newest first when duplicates exist).
    Never returns ``hero_id``.
    """
    wp, auth = wp_auth(site)
    prefix = (site.get("prefix") or "").strip()
    slug = re.sub(r"[^a-z0-9-]+", "-", (topic_slug or "topic").lower()).strip("-") or "topic"
    need = f"{prefix}-{slug}-social"
    r = requests.get(
        f"{wp}/wp-json/wp/v2/media",
        auth=auth,
        params={"search": need, "per_page": 80, "orderby": "date", "order": "desc"},
        timeout=30,
    )
    r.raise_for_status()
    hid = int(hero_id)
    best: Optional[int] = None
    best_dt = ""
    for item in r.json():
        if int(item["id"]) == hid:
            continue
        tit = (item.get("title") or {}).get("raw") or (item.get("title") or {}).get("rendered") or ""
        tit = tit.strip()
        if tit.lower() == need.lower():
            dt = (item.get("date") or "").strip()
            if dt >= best_dt:
                best_dt = dt
                best = int(item["id"])
    return best


def resolve_social_id(wp: str, auth, post_id: int, hero_id: int) -> Optional[int]:
    aioseo = requests.get(
        f"{wp}/wp-json/aioseo/v1/post?postId={post_id}",
        auth=auth,
        timeout=30,
    ).json()
    og = aioseo.get("data", {}).get("currentPost", {}).get("og_image_custom_url") or ""
    if not og:
        return None
    base = _basename_media_path(og)
    if re.search(r"-hero\.(jpe?g|png|webp)$", base, re.I):
        return None
    m = re.search(r"/([^/]+)\.(?:jpg|jpeg|png|webp)(?:\?|$)", og, re.I)
    slug = m.group(1) if m else None
    if slug:
        r = requests.get(
            f"{wp}/wp-json/wp/v2/media",
            auth=auth,
            params={"search": slug, "per_page": 30},
            timeout=30,
        )
        r.raise_for_status()
        for item in r.json():
            tit = (item.get("title") or {}).get("raw") or (item.get("title") or {}).get("rendered") or ""
            if tit.lower().endswith("-hero"):
                continue
            su = item.get("source_url") or ""
            if slug in su or slug.replace("-1", "") in su:
                if item["id"] != hero_id:
                    return int(item["id"])
    return None


def _cap_raw(media: dict) -> str:
    c = media.get("caption") or {}
    return c.get("raw") or c.get("rendered") or ""


def extract_google_doc_body_inner_html(ghtml: str) -> str:
    """
    Article HTML taken directly from the Google Docs export (no LLM).
    Drops ``<head>`` and ``body``-level ``script``/``style``; keeps the Doc's body markup as-is.
    """
    soup = BeautifulSoup(ghtml or "", "html.parser")
    head = soup.find("head")
    if head:
        head.decompose()
    body = soup.body
    if not body:
        return (ghtml or "").strip()
    for junk in list(body.find_all(["script", "style"])):
        junk.decompose()
    parts: List[str] = []
    for child in body.children:
        if isinstance(child, Comment):
            continue
        chunk = str(child).strip()
        if chunk:
            parts.append(chunk)
    return "\n".join(parts).strip() or (ghtml or "").strip()


def planner_plaintext_excerpt_from_gdoc(ghtml: str, *, max_chars: int) -> str:
    """
    Plain text from the Doc body for Claude metadata planning only (not the published HTML body).
    """
    inner = extract_google_doc_body_inner_html(ghtml)
    t = BeautifulSoup(inner, "html.parser").get_text("\n", strip=True)
    t = unicodedata.normalize("NFKC", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[: max(1, int(max_chars))]


def _cd_anchor_should_wrap_strong(a) -> bool:
    parent = getattr(a, "parent", None)
    if bool(a.find("strong") or a.find("b")):
        return False
    if parent is not None and getattr(parent, "name", "") in ("strong", "b"):
        return False
    return True


def _cd_wrap_anchor_contents_in_strong(soup: BeautifulSoup, a) -> bool:
    """
    Wrap ``a``'s inner markup in ``<strong>`` when required for CD sponsored link QA.
    Handles Google Docs exports where anchor text lives only in nested ``span``s or ``&nbsp;`` padding.
    """
    if not _cd_anchor_should_wrap_strong(a):
        return False
    inner = a.decode_contents()
    # Do not use ``inner.strip()`` — Google Docs uses NBSP-only anchors; str.strip removes ``\xa0``.
    if inner is None or inner == "":
        return False
    a.clear()
    strong = soup.new_tag("strong")
    frag = BeautifulSoup(inner, "html.parser")
    container = frag.body if frag.body else frag
    for child in list(container.children):
        if hasattr(child, "extract"):
            strong.append(child.extract())
        else:
            strong.append(child)
    a.append(strong)
    return True


def canonicalize_body_http_links_cd(site: dict, body_html: str) -> str:
    """
    CD sponsored contract: every ``http(s)`` body anchor is dofollow, ``target=_blank``,
    and wraps anchor text in ``<strong>`` (same shape as ``verify_sponsored_body_links``).
    Does not change hrefs or anchor wording.
    """
    if site.get("key") != "cd" or not (body_html or "").strip():
        return body_html
    soup = BeautifulSoup(body_html, "html.parser")
    changed = False
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        if not re.match(r"https?://", href, re.I):
            continue
        rel = a.get("rel")
        rel_s = " ".join(rel) if isinstance(rel, list) else (rel or "")
        if "nofollow" in rel_s.lower():
            if isinstance(rel, list):
                parts = [x for x in rel if str(x).lower() != "nofollow"]
            else:
                parts = [x for x in str(rel or "").split() if x.lower() != "nofollow"]
            if parts:
                a["rel"] = parts
            elif "rel" in a.attrs:
                del a["rel"]
            changed = True
        if (a.get("target") or "").lower() != "_blank":
            a["target"] = "_blank"
            changed = True
        if _cd_wrap_anchor_contents_in_strong(soup, a):
            changed = True
    return str(soup) if changed else body_html


def _html_before_machine_tail(raw: str) -> str:
    """Strip machine citation + donation tail so body-only <a> tags can be audited."""
    if "<!--scoutmonkeys-machine-tail-->" in raw:
        return raw.split("<!--scoutmonkeys-machine-tail-->", 1)[0]
    m = re.search(r'<p><em><a href="https://www\.pexels\.com[^>]*>', raw, re.I)
    if m:
        return raw[: m.start()]
    if "CLICK HERE TO DONATE" in raw:
        return raw.split("CLICK HERE TO DONATE", 1)[0]
    return raw


def normalize_cd_body_support_links_for_dofollow(site: dict, body_html: str) -> str:
    """
    Google Docs sometimes export in-body links to culturaldaily.com/support/ with
    rel=nofollow. Sponsored QA requires dofollow body links; the appended donation tail
    keeps its own nofollow. This only touches anchors whose href points at that support URL.
    Does not change anchor text or href (CRITICAL wording preserved).
    """
    if site.get("key") != "cd" or not (body_html or "").strip():
        return body_html
    soup = BeautifulSoup(body_html, "html.parser")
    changed = False
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        if not re.match(r"https?://", href, re.I):
            continue
        if "culturaldaily.com" not in href.lower() or "/support" not in href.lower():
            continue
        rel = a.get("rel")
        rel_s = " ".join(rel) if isinstance(rel, list) else (rel or "")
        if "nofollow" in rel_s.lower():
            if isinstance(rel, list):
                parts = [x for x in rel if str(x).lower() != "nofollow"]
            else:
                parts = [x for x in str(rel or "").split() if x.lower() != "nofollow"]
            if parts:
                a["rel"] = parts
            elif "rel" in a.attrs:
                del a["rel"]
            changed = True
        if (a.get("target") or "").lower() != "_blank":
            a["target"] = "_blank"
            changed = True
        if _cd_wrap_anchor_contents_in_strong(soup, a):
            changed = True
    return str(soup) if changed else body_html


def verify_sponsored_body_links(html_before_tail: str) -> Tuple[bool, str]:
    """
    Every outbound body http(s) link: dofollow, target=_blank, bold inner anchor (hard site rule).
    """
    soup = BeautifulSoup(html_before_tail, "html.parser")
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        if not re.match(r"https?://", href, re.I):
            continue
        rel = a.get("rel")
        rel_s = " ".join(rel) if isinstance(rel, list) else (rel or "")
        if "nofollow" in rel_s.lower():
            return False, f"nofollow on {href[:80]}"
        if (a.get("target") or "").lower() != "_blank":
            return False, f"missing target=_blank on {href[:80]}"
        inner_bold = bool(a.find("strong") or a.find("b"))
        parent = getattr(a, "parent", None)
        outer_bold = parent is not None and getattr(parent, "name", "") in ("strong", "b")
        if not inner_bold and not outer_bold:
            return False, f"missing bold wrapper on {href[:80]}"
    return True, ""


def verify_post(
    site: dict,
    post_id: int,
    seo: dict,
    hero_id: int,
    social_id: int,
    title_max: int,
    *,
    expect_exact_title: Optional[str] = None,
    critical_rules: bool = False,
) -> bool:
    """
    Run QA checks aligned with QA.md / CLAUDE.md / CRITICAL_RULES.md.
    The `seo` dict is accepted for backwards compatibility; live values are read from WP.
    """
    print(f"\n[QA] Verifying post {post_id}…")
    wp, auth = wp_auth(site)
    prefix = site["prefix"]

    post = requests.get(
        f"{wp}/wp-json/wp/v2/posts/{post_id}?context=edit", auth=auth, timeout=30
    ).json()
    hero = requests.get(
        f"{wp}/wp-json/wp/v2/media/{hero_id}?context=edit", auth=auth, timeout=30
    ).json()
    soc = requests.get(
        f"{wp}/wp-json/wp/v2/media/{social_id}?context=edit", auth=auth, timeout=30
    ).json()
    seo_r = requests.get(
        f"{wp}/wp-json/cd-seo/v1/read?post_id={post_id}", auth=auth, timeout=30
    ).json()
    c = post["content"]["raw"]
    pre_tail = _html_before_machine_tail(c)
    sponsored_links_ok, sponsored_note = verify_sponsored_body_links(pre_tail)

    checks: List[Tuple[str, bool]] = []

    def chk(label: str, ok: bool, note: str = "") -> None:
        icon = "✅" if ok else "❌"
        print(f"  {icon} {label}" + (f"  [{note}]" if note else ""))
        checks.append((label, ok))

    raw_title = post["title"]["raw"]
    if expect_exact_title:
        chk(
            "Post title matches extracted H1 (CRITICAL_RULES)",
            raw_title == expect_exact_title,
            f"{len(raw_title)} chars vs expected {len(expect_exact_title)}",
        )
    else:
        chk(f"Post title ≤{title_max} chars", len(raw_title) <= title_max, f"{len(raw_title)} chars")

    seo_title = seo_r.get("aioseo_db", {}).get("title") or ""
    if site.get("key") == "cd":
        exp_seo = build_cd_aioseo_seo_title(raw_title, ((seo or {}).get("seo_title") or "").strip())
        chk(
            "SEO title matches CD AIOSEO rule (≤60, word-safe, optional suffix)",
            (seo_title or "").strip() == exp_seo,
            f"{len(seo_title or '')} chars",
        )
    elif critical_rules and expect_exact_title:
        chk(
            "SEO title matches H1 (CRITICAL_RULES)",
            seo_title == expect_exact_title or seo_title == raw_title,
            f"{len(seo_title)} chars",
        )
    else:
        chk(
            f"SEO title ≤{site['seo_title_max']} chars",
            0 < len(seo_title) <= site["seo_title_max"],
            f"{len(seo_title)} chars",
        )

    meta = seo_r.get("aioseo_db", {}).get("description") or ""
    if site.get("key") == "cd":
        chk(
            "Meta description 120–160 chars (CD)",
            META_DESCRIPTION_MIN <= len(meta) <= META_DESCRIPTION_MAX,
            f"{len(meta)} chars",
        )
    else:
        chk("Meta description ≤160 chars", 0 < len(meta) <= 160, f"{len(meta)} chars")

    try:
        kw = json.loads(seo_r["aioseo_db"].get("keyphrases") or "{}").get("focus", {}).get(
            "keyphrase", ""
        )
    except Exception:
        kw = ""
    chk("Focus keyword set", bool(kw), f"'{kw}'")
    body_plain_for_kw = BeautifulSoup(pre_tail, "html.parser").get_text(" ", strip=True)
    if site.get("key") == "cd":
        kw_words = len(kw.split()) if kw else 0
        kws = focus_keyword_content_score(kw, body_plain_for_kw, title=raw_title)
        chk(
            "Focus keyword 1–2 words, score ≥82 (CD)",
            bool(kw) and 0 < kw_words <= 2 and kws >= 81.99,
            f"{kw_words} words, score={kws:.1f}",
        )
    elif critical_rules:
        kw_words = len(kw.split()) if kw else 0
        chk(
            "Focus keyword short (CRITICAL_RULES)",
            bool(kw) and kw_words <= 5 and len(kw) <= 56,
            f"{kw_words} words, {len(kw)} chars",
        )

    chk("Featured image = hero", post.get("featured_media") == hero_id)

    hw = hero.get("media_details", {}).get("width", 0)
    hh = hero.get("media_details", {}).get("height", 0)
    chk(
        f"Hero {site['hero_w']}×{site['hero_h']}",
        hw == site["hero_w"] and hh == site["hero_h"],
        f"{hw}×{hh}",
    )

    sw = int((soc.get("media_details") or {}).get("width") or 0)
    sh = int((soc.get("media_details") or {}).get("height") or 0)
    if site.get("key") == "cd":
        chk(
            "Social attachment stored at 1920×1400 (CD; host must not downscale)",
            sw == site["social_w"] and sh == site["social_h"],
            f"{sw}×{sh}",
        )

    ht = hero.get("title") or {}
    h_title = ht.get("raw") or ht.get("rendered") or ""
    chk(
        f"Hero title ({prefix}-...-hero)",
        h_title.startswith(prefix + "-") and h_title.endswith("-hero"),
        f"'{h_title}'",
    )

    h_alt = hero.get("alt_text") or ""
    if site.get("key") == "cd":
        chk(
            "Hero alt describes the photo (not the post title)",
            len(h_alt) > 8 and _norm_title_text(h_alt) != _norm_title_text(raw_title),
            f'"{h_alt[:70]}"',
        )
    else:
        chk("Hero alt text descriptive (>10 chars)", len(h_alt) > 10, f'"{h_alt[:50]}"')

    h_cap = _cap_raw(hero)
    if critical_rules:
        chk(
            "Hero caption empty or Photo credit (CRITICAL_RULES)",
            h_cap == "" or h_cap.startswith("Photo:"),
            repr(h_cap[:80]),
        )
    else:
        chk("Hero caption starts 'Photo:'", h_cap.startswith("Photo:"), f'"{h_cap}"')

    st = soc.get("title") or {}
    s_title = st.get("raw") or st.get("rendered") or ""
    chk(
        f"Social title ({prefix}-...-social)",
        s_title.startswith(prefix + "-") and s_title.endswith("-social"),
        f"'{s_title}'",
    )

    s_alt = soc.get("alt_text") or ""
    if site.get("key") == "cd":
        chk(
            "Social alt describes the photo (not the post title)",
            len(s_alt) > 8 and _norm_title_text(s_alt) != _norm_title_text(raw_title),
            f'"{s_alt[:70]}"',
        )
    else:
        chk("Social alt matches hero", s_alt == h_alt)

    s_cap = _cap_raw(soc)
    if critical_rules:
        chk(
            "Social caption empty or Photo credit (CRITICAL_RULES)",
            s_cap == "" or s_cap.startswith("Photo:"),
            repr(s_cap[:80]),
        )
    else:
        chk("Social caption starts 'Photo:'", s_cap.startswith("Photo:"), f'"{s_cap}"')

    aioseo_post = requests.get(
        f"{wp}/wp-json/aioseo/v1/post?postId={post_id}", auth=auth, timeout=30
    ).json()
    curp = (aioseo_post.get("data") or {}).get("currentPost") or {}
    og = curp.get("og_image_custom_url") or ""
    og_cd = (seo_r.get("aioseo_db") or {}).get("og_image_url") or ""
    og_ok = bool(og or og_cd)
    og_note = (og or og_cd)[-50:] if og_ok else "missing"
    chk("Social set as OG image (AIOSEO / cd-seo)", og_ok, og_note)
    s_url = (soc.get("source_url") or "").strip()
    if site.get("key") == "cd" and s_url:
        og_pick = og or og_cd
        og_b = _normalize_wp_media_basename(og_pick)
        soc_b = _normalize_wp_media_basename(s_url)
        chk(
            "OG / custom image URL basename is the social JPEG (not hero)",
            bool(og_pick) and og_b == soc_b and "-social" in og_b and "-hero" not in og_b,
            f"{og_b!r} vs {soc_b!r}",
        )
    if site.get("key") == "cd":
        og_type = (curp.get("og_image_type") or "").strip().lower()
        chk(
            "AIOSEO OG image type is custom",
            og_type in ("custom", "custom_image") or bool(og),
            og_type or "n/a",
        )

    if site.get("key") == "cd" and critical_rules:
        cat_ids: List[int] = []
        for x in post.get("categories") or []:
            try:
                cat_ids.append(int(x))
            except (TypeError, ValueError):
                continue
        bad_slug = ""
        if cat_ids:
            rcat = requests.get(
                f"{wp}/wp-json/wp/v2/categories",
                auth=auth,
                params={"include": ",".join(str(x) for x in cat_ids[:50]), "per_page": 50},
                timeout=30,
            )
            if rcat.ok:
                for row in rcat.json():
                    slug = row.get("slug") or ""
                    name = row.get("name") or ""
                    if cd_sponsor_category_forbidden(slug, name):
                        bad_slug = slug or name
                        break
        chk(
            "Post categories: no Sponsored / Featured Story (CD)",
            not bad_slug,
            bad_slug or "ok",
        )

    chk(
        "Sponsored body links (bold, target=_blank, no nofollow)",
        sponsored_links_ok,
        sponsored_note,
    )

    chk("Paid links bold in content", "<strong>" in c)

    cite_pexels = bool(
        re.search(
            r'<p><em><a href="https://www\.pexels\.com[^"]*"[^>]*>Photo: .+ via Pexels</a></em></p>',
            c,
            re.I,
        )
    )
    cite_other = bool(
        re.search(
            r'<p><em><a href="https?://[^"]+"[^>]*rel="nofollow noopener"[^>]*>Photo:\s*.+</a></em></p>',
            c,
            re.I,
        )
    )
    cite_client_plain = bool(re.search(r"<p><em>Photo:\s*.+</em></p>", c, re.I))
    cite_client_none = False
    if "<!--scoutmonkeys-machine-tail-->" in c:
        rest = c.split("<!--scoutmonkeys-machine-tail-->", 1)[1]
        pre_hr = rest.split("<hr", 1)[0] if "<hr" in rest.lower() else rest
        cite_client_none = ("<p><em>" not in pre_hr) and ("photo:" not in pre_hr.lower())
    cite_ok = cite_pexels or cite_other or cite_client_plain or cite_client_none
    chk("Citation (Pexels / client / none-marker)", cite_ok)
    chk("Citation NOT bold", "<strong>Photo:" not in c)

    chk("Horizontal rule <hr />", "<hr />" in c)
    chk("No <!--nextpage--> page break", "<!--nextpage-->" not in c)

    chk(
        "Donation box present (canonical CD CTA)",
        (DONATION_CTA_TEXT_CD in c) or ("CLICK HERE TO DONATE" in c),
        "",
    )

    if "<!--scoutmonkeys-machine-tail-->" in c:
        tail_rest = c.split("<!--scoutmonkeys-machine-tail-->", 1)[1]
        m_hr = re.search(r"<hr\s*/?>", tail_rest, re.I)
        hp = m_hr.start() if m_hr else -1
        rest_after_hr = tail_rest[m_hr.end() :].lstrip() if m_hr else ""
        # Nothing between ``<hr />`` and the donation block except whitespace — donation opens with ``<p>``.
        hr_donation_gap_ok = bool(
            m_hr and rest_after_hr.lower().startswith("<p>") and DONATION_CTA_TEXT_CD in tail_rest
        )
        dp = tail_rest.find(DONATION_CTA_TEXT_CD)
        if dp < 0:
            dp = tail_rest.find("CLICK HERE TO DONATE")
        pre_hr_seg = tail_rest[:hp] if m_hr and hp > 0 else ""
        tail_cite_ok = (not pre_hr_seg.strip()) or ("<p><em>" in pre_hr_seg)
        chk(
            "Order: citation (optional) → hr → donation; nothing between hr and donation",
            hp >= 0 and dp >= hp and hr_donation_gap_ok and tail_cite_ok,
            f"hr@{hp} don@{dp}",
        )
    else:
        cp = c.find("Photo:")
        hp = c.find("<hr />")
        dp = c.find("CLICK HERE TO DONATE")
        chk("Order: citation → hr → donation", 0 <= cp < hp < dp, f"{cp}→{hp}→{dp}")

    passed = sum(1 for _, ok in checks if ok)
    total = len(checks)
    print(f"\n  {'='*45}")
    print(f"  QA: {passed}/{total} passed {'✅ ALL GOOD' if passed == total else '❌ FIX REQUIRED'}")
    if passed < total:
        print("  FAILED:", ", ".join(lb for lb, ok in checks if not ok))
    print(f"  {'='*45}\n")
    return passed == total


def _apply_repo_dotenv_for_cli() -> None:
    """Load `REPO_ROOT/.env` into os.environ (used by `run()`, remediate CLI, and local `python pipeline.py` runs)."""
    p = REPO_ROOT / ".env"
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        os.environ[k] = v


def _parse_topic_slug_from_attachment_title(title: str, prefix: str) -> str:
    t = (title or "").strip()
    pre = f"{prefix}-"
    for suf in ("-hero", "-social"):
        tl, pl, sl = t.lower(), pre.lower(), suf.lower()
        if tl.startswith(pl) and tl.endswith(sl):
            mid = t[len(pre) : -len(suf)]
            return (mid.strip("-") or "topic").lower()
    return "topic"


def remediate_latest_cd_draft() -> dict:
    """
    Repair the newest Cultural Daily (Our Friends) draft per CRITICAL_RULES when possible:
    set **category** from audit-backed resolution + title/body lane inference (defaults to
    **Check This Out**), title-aligned short focus keyphrase (score ≥82 heuristic), SEO title = post title, ensure OG social URL
    is set, and re-upload social JPEG at exact 1920×1400 from the current featured hero if the
    existing social attachment is missing or wrong size.
    """
    if not critical_rules_active():
        raise RuntimeError("CRITICAL_RULES.md is missing.")
    _apply_repo_dotenv_for_cli()
    _refresh_runtime_env_from_os()
    _refresh_sites()
    site = SITES["cd"]
    if not site.get("wp_pass"):
        raise RuntimeError("WP_USER / WP_PASS not set (add .env or export credentials).")

    wp, auth = wp_auth(site)
    r = requests.get(
        f"{wp}/wp-json/wp/v2/posts",
        auth=auth,
        params={
            "status": "draft",
            "per_page": 20,
            "orderby": "date",
            "order": "desc",
        },
        timeout=30,
    )
    r.raise_for_status()
    posts = r.json()
    aid = int(site["author_id"])
    ours = [p for p in posts if int(p.get("author") or 0) == aid]
    if not ours and posts:
        ours = posts
        print(
            f"[remediate] No drafts with author={aid} in latest page (CD REST ?author= is unreliable); "
            f"using newest draft author={posts[0].get('author')}."
        )
    if not ours:
        raise RuntimeError("No drafts returned from WordPress.")
    post_id = int(ours[0]["id"])
    pe = requests.get(
        f"{wp}/wp-json/wp/v2/posts/{post_id}?context=edit",
        auth=auth,
        timeout=30,
    )
    pe.raise_for_status()
    post = pe.json()
    hero_id = int(post.get("featured_media") or 0)
    if not hero_id:
        raise RuntimeError(f"Post {post_id} has no featured_media — cannot remediate images/SEO.")

    hero = requests.get(
        f"{wp}/wp-json/wp/v2/media/{hero_id}?context=edit",
        auth=auth,
        timeout=30,
    ).json()
    hero_url = (hero.get("source_url") or "").strip()
    if not hero_url:
        raise RuntimeError("Featured image has no source_url")

    h_title = (hero.get("title") or {}).get("raw") or (hero.get("title") or {}).get("rendered") or ""
    slug = _parse_topic_slug_from_attachment_title(h_title, site["prefix"])
    raw_title = (post.get("title") or {}).get("raw") or (post.get("title") or {}).get("rendered") or ""
    hero_alt = (hero.get("alt_text") or "").strip()
    caph = _cap_raw(hero)
    used_pex = "pexels" in caph.lower()
    alt = hero_social_alt_for_cd(
        planner_alt=hero_alt if hero_alt and _norm_title_text(hero_alt) != _norm_title_text(raw_title) else "",
        post_title=raw_title,
        used_client_hero=not used_pex,
        hero_pexels_query=slug.replace("-", " "),
        topic_slug=slug,
    )

    actions: List[str] = []

    marker = "<!--scoutmonkeys-machine-tail-->"
    raw_content = (post.get("content") or {}).get("raw") or ""
    if marker in raw_content:
        pre_body, tail_part = raw_content.split(marker, 1)
        tail_suffix = marker + tail_part
    else:
        pre_body = raw_content
        tail_suffix = ""
    pre_body = normalize_cd_body_vertical_spacing(pre_body)
    pre_body = cd_resolve_gdoc_footnote_images(pre_body)
    pre_body = cd_strip_residual_footnote_url_paragraphs(pre_body)
    if not used_pex:
        pre_body = cd_relocate_lead_images_after_substantive_opening(
            pre_body, used_client_hero=True
        )
    pre2 = normalize_cd_body_support_links_for_dofollow(site, pre_body)
    pre2 = cd_deduplicate_inline_body_images(pre2, hero_src_to_skip=hero_url)
    pre2 = cd_reupload_inline_body_images(
        site,
        pre2,
        topic_slug=slug,
        post_title=raw_title,
        hero_src_to_skip=hero_url,
    )
    pre2 = cd_promote_gdoc_heading_paragraphs(pre2)
    pre2 = cd_format_body_inline_images(pre2, post_title=raw_title, site=site)
    pre2 = format_to_audit_standard(pre2, site=site)
    cd_sync_inline_attachment_alts_from_body(site, pre2)
    new_content = pre2.rstrip() + (("\n\n" + tail_suffix) if tail_suffix else "")
    # Always persist processed HTML on remediate: strict string compare misses WP serialization drift and
    # leaves the block editor on old ``P > span > img`` markup while QA passes on REST ``raw``.
    skip_body = (os.environ.get("REMEDIATE_SKIP_BODY_POST") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not skip_body:
        rub = requests.post(
            f"{wp}/wp-json/wp/v2/posts/{post_id}",
            auth=auth,
            json={"content": new_content},
            timeout=120,
        )
        rub.raise_for_status()
        actions.append(
            "post body: pipeline HTML synced (CD-InsertN, dedupe, centered figures, audit format)"
        )
        raw_content = new_content
        post = requests.get(
            f"{wp}/wp-json/wp/v2/posts/{post_id}?context=edit",
            auth=auth,
            timeout=30,
        ).json()

    sid = find_social_attachment_by_title(site, slug, hero_id)
    regen_social = False
    if not sid:
        regen_social = True
    else:
        soc = requests.get(
            f"{wp}/wp-json/wp/v2/media/{int(sid)}?context=edit",
            auth=auth,
            timeout=30,
        ).json()
        sw = int((soc.get("media_details") or {}).get("width") or 0)
        sh = int((soc.get("media_details") or {}).get("height") or 0)
        if sw != site["social_w"] or sh != site["social_h"]:
            regen_social = True

    excerpt_for_cat = BeautifulSoup(raw_content, "html.parser").get_text(" ", strip=True)[:8000]
    cat_lane = infer_cd_sponsor_category_hint(
        "",
        topic_slug=slug,
        title=raw_title,
        plaintext_excerpt=excerpt_for_cat,
    )
    cat_id, cat_note = resolve_cd_sponsored_category(
        site, category_hint=cat_lane, topic_slug=slug, title=raw_title
    )
    rp = requests.post(
        f"{wp}/wp-json/wp/v2/posts/{post_id}",
        auth=auth,
        json={"categories": [cat_id]},
        timeout=60,
    )
    rp.raise_for_status()
    actions.append(f"categories=[{cat_id}] {cat_note}")

    if regen_social:
        pil = _download_image(hero_url).convert("RGB")
        social_img = _resize_cover_exact_floor(pil, site["social_w"], site["social_h"])
        assert social_img.size == (site["social_w"], site["social_h"]), social_img.size
        cap = _cap_raw(hero)
        if cap and not cap.startswith("Photo:"):
            cap = f"Photo: {cap}" if "pexels" in cap.lower() else ""
        prefix = site["prefix"]
        social_fn = f"{prefix}-{slug}-social.jpg"
        sm = wp_upload_jpeg(
            site,
            social_img,
            social_fn,
            f"{prefix}-{slug}-social",
            alt,
            cap,
        )
        sid = int(sm["id"])
        r_sv = requests.get(
            f"{wp}/wp-json/wp/v2/media/{sid}?context=edit",
            auth=auth,
            timeout=30,
        )
        r_sv.raise_for_status()
        assert_cd_social_attachment_stored_dimensions(
            site, r_sv.json(), context="remediate social reupload"
        )
        sm = r_sv.json()
        social_url = (sm.get("source_url") or "").strip()
        actions.append(f"reuploaded social media id={sid} {site['social_w']}×{site['social_h']}")
    else:
        soc = requests.get(
            f"{wp}/wp-json/wp/v2/media/{int(sid)}?context=edit",
            auth=auth,
            timeout=30,
        ).json()
        assert_cd_social_attachment_stored_dimensions(
            site, soc, context="remediate existing social attachment"
        )
        social_url = (soc.get("source_url") or "").strip()

    if not social_url:
        raise RuntimeError("Could not resolve social image URL for AIOSEO.")

    r_alt = requests.post(
        f"{wp}/wp-json/wp/v2/media/{int(sid)}",
        auth=auth,
        json={"alt_text": alt},
        timeout=60,
    )
    if r_alt.ok:
        actions.append("social media alt_text (photo description, not title)")
    else:
        print(f"[warn] social alt_text PATCH {r_alt.status_code}: {r_alt.text[:200]}")

    seo_r = requests.get(
        f"{wp}/wp-json/cd-seo/v1/read?post_id={post_id}",
        auth=auth,
        timeout=30,
    ).json()
    try:
        kw = json.loads((seo_r.get("aioseo_db") or {}).get("keyphrases") or "{}").get("focus", {}).get(
            "keyphrase", ""
        )
    except Exception:
        kw = ""
    focus_seed = (kw or slug.replace("-", " ")).strip()
    focus = refine_focus_keyword_for_content(
        focus_seed,
        body=pre2,
        doc_html="",
        title=raw_title,
        topic_slug=slug,
    )
    raw_plain = BeautifulSoup(raw_content, "html.parser").get_text(" ", strip=True)
    meta_raw = (seo_r.get("aioseo_db") or {}).get("description") or ""
    meta = ensure_meta_description_length(meta_raw or raw_title, raw_plain)
    seo = {
        "focus_keyword": focus,
        "seo_title": build_cd_aioseo_seo_title(raw_title, ""),
        "meta_description": meta,
        "excerpt": meta,
    }
    push_aioseo_and_cdseo(
        site,
        post_id,
        seo,
        social_url,
        seo_title_max=60,
    )
    actions.append("aioseo+cd-seo: focus ≤2 words, CD seo_title/meta, og_image set")

    qa = verify_post(
        site,
        post_id,
        seo,
        hero_id,
        int(sid),
        site["title_max"],
        expect_exact_title=raw_title if critical_rules_active() else None,
        critical_rules=True,
    )
    edit_url = f"{site['wp_url'].rstrip('/')}/wp-admin/post.php?post={post_id}&action=edit"
    print("[remediate] WhatsApp notification (draft updated)…")
    send_whatsapp(
        post_id,
        raw_title,
        edit_url,
        site["site_label"],
        qa_ok=qa,
        extra_line="Pipeline: remediate-latest cd (draft refreshed).",
    )
    actions.append("whatsapp: draft notification sent (if Twilio configured)")
    return {"post_id": post_id, "hero_id": hero_id, "social_id": int(sid), "actions": actions, "qa_ok": qa}


def send_whatsapp(
    post_id: int,
    title: str,
    edit_url: str,
    site_label: str,
    *,
    qa_ok: Optional[bool] = None,
    extra_line: str = "",
) -> None:
    if not TWILIO_SID or not TWILIO_TOKEN or not TWILIO_FROM:
        print("[10] ⚠ WhatsApp skipped — TWILIO creds not set")
        return
    if TWILIO_SID == "TWILIO_ACCOUNT_SID" or TWILIO_TOKEN == "TWILIO_AUTH_TOKEN":
        print("[10] ⚠ WhatsApp skipped — Twilio env vars are still Railway placeholders")
        return
    to = WA_TO or f"whatsapp:{WA_PHONE}"
    qa_line = ""
    if qa_ok is True:
        qa_line = "\nQA: all checks passed."
    elif qa_ok is False:
        qa_line = "\nQA: some checks failed — open the draft in WordPress."
    extra = f"\n{(extra_line or '').strip()}" if (extra_line or "").strip() else ""
    msg = (
        f"✅ Draft saved — {site_label}\n"
        f"\"{title}\"\n"
        f"ID: {post_id}\n"
        f"Edit: {edit_url}"
        f"{qa_line}"
        f"{extra}"
    )
    print(f"[10] Sending WhatsApp to {to}…")
    r = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json",
        auth=(TWILIO_SID, TWILIO_TOKEN),
        data={"From": TWILIO_FROM, "To": to, "Body": msg},
        timeout=30,
    )
    if not r.ok:
        print(f"[10] Twilio error {r.status_code}: {r.text[:400]}")
    else:
        print("[10] WhatsApp sent.")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(gdoc_url: str, site_key: str = "cd") -> dict:
    _apply_repo_dotenv_for_cli()
    _refresh_runtime_env_from_os()
    _refresh_sites()
    site_key = site_key.lower().strip()
    if site_key not in SITES:
        raise ValueError(f"Unknown site {site_key!r}; choose from {list(SITES)}")
    site = SITES[site_key]
    if not site.get("wp_pass"):
        raise RuntimeError(f"Missing WordPress credentials for {site_key}")

    print(f"[1] Fetching Google Doc…")
    ghtml = fetch_gdoc_html(gdoc_url)

    print(f"[1b] Doc intake parse (cultural_daily_sponsored_rules contract)…")
    intake = doc_parser.parse_google_doc_intake(ghtml, source_url=gdoc_url)
    summ = intake.get("summary") or {}
    tab_note = ""
    if intake.get("gdoc_tab_id"):
        tab_note = f" tab={intake['gdoc_tab_id']!r}"
    print(
        f"     images={summ.get('image_count')} credits={summ.get('photo_credit_block_count')} "
        f"links={summ.get('hyperlink_count')} "
        f"body_links_not_canonical={summ.get('body_links_not_canonical_count', 0)}{tab_note}"
    )
    flags = intake.get("contract_flags") or []
    if flags:
        print(f"     contract_flags: {', '.join(flags)}")

    cr = critical_rules_active()
    machine_h1 = extract_h1_from_gdoc_html(ghtml).strip()
    client_src = first_client_image_src_from_gdoc(ghtml)
    manual_flags: List[str] = []
    if cr and site["key"] == "cd" and not OUR_FRIENDS_AUDIT_JSON.is_file():
        manual_flags.append("our_friends_audit_json_missing_local")
    if cr and site["key"] == "cd" and not machine_h1:
        raise RuntimeError(
            "CRITICAL_RULES.md is active but no H1 could be extracted from the Google Doc "
            "(no <h1>, no title-styled paragraph, or empty <title>). Rule #1 requires the exact "
            "client H1 — fix the Doc structure before publishing."
        )

    print(f"[2] Planning layout with Anthropic…")
    if cr:
        print("     CRITICAL_RULES.md is present — enforcing verbatim H1, faithful body, client hero, category.")
    plan = plan_from_gdoc_html(
        site,
        ghtml,
        intake=intake,
        critical_rules=cr,
        machine_h1=machine_h1,
        client_image_src=client_src,
    )
    topic = re.sub(r"[^a-z0-9-]+", "-", (plan.get("topic_slug") or "topic").lower()).strip("-")
    title = (plan.get("post_title") or "Untitled").strip()
    focus = (plan.get("focus_keyword") or "").strip()
    seo_title = (plan.get("seo_title") or title).strip()
    meta = (plan.get("meta_description") or "").strip()[:160]
    hero_q = (plan.get("hero_pexels_query") or title).strip()
    cat_hint = (plan.get("category_hint") or "").strip() or "culture"

    if cr:
        if machine_h1:
            if title != machine_h1:
                print(f"[2c] Forcing post_title to extracted H1 (was {len(title)} chars, expected exact match)")
                manual_flags.append("forced_h1_from_extract")
            title = machine_h1
        if site["key"] == "cd":
            focus = compact_focus_keyword(focus or topic.replace("-", " "), max_words=2, max_len=36)
        else:
            focus = compact_focus_keyword(focus or topic.replace("-", " "))
        if not meta:
            meta = derive_meta_from_gdoc_first_paragraph(ghtml)
        if site["key"] != "cd":
            seo_title = title
        if site["key"] == "cd":
            raw_cat = (plan.get("category_hint") or "").strip() or "Check This Out"
            cat_hint = infer_cd_sponsor_category_hint(
                raw_cat,
                topic_slug=topic,
                title=title,
                plaintext_excerpt=planner_plaintext_excerpt_from_gdoc(ghtml, max_chars=12_000),
            )
            if _slugify_category_hint(cat_hint) != _slugify_category_hint(raw_cat):
                print(f"[2d] category_hint refined: {raw_cat!r} → {cat_hint!r}")
        else:
            cat_hint = "Check This Out"
        if client_src:
            hero_q = ""

    if not cr and site["key"] != "cd":
        seo_title = seo_title[: site["seo_title_max"]]

    if site["key"] == "cd":
        excerpt_long = planner_plaintext_excerpt_from_gdoc(ghtml, max_chars=80_000)
        base_meta = (meta or "").strip() or derive_meta_from_gdoc_first_paragraph(ghtml)
        meta = ensure_meta_description_length(base_meta, excerpt_long)
        seo_title = build_cd_aioseo_seo_title(title, (plan.get("seo_title") or "").strip())

    # CRITICAL_RULES #2 / operator contract: article HTML never from Claude — only Doc export + code normalization.
    print("[2a] Article body from Google Doc HTML export (Claude article_body_html ignored).")
    manual_flags.append("article_body_source:google_doc_export")
    body = extract_google_doc_body_inner_html(ghtml)
    if site["key"] == "cd":
        try:
            from corpus_compare import compare_doc_to_corpora

            cc = compare_doc_to_corpora(body)
            wns = cc.get("warnings") or []
            if wns:
                print(
                    f"[1c] Corpus scorecard ({len(wns)} note(s); "
                    f"data/gdoc_intake_profile.json vs audit_format_profile.json)…"
                )
                for w in wns[:12]:
                    print(f"     ⚠ {w}")
                manual_flags.append("corpus_scorecard:see_console")
        except Exception as ex:
            print(f"[1c] Corpus scorecard skipped: {ex}")
    if site["key"] == "cd":
        body = normalize_cd_body_vertical_spacing(body)
    if site["key"] == "cd" and client_src:
        body = remove_client_hero_image_from_body_html(body, client_src)
    if site["key"] == "cd" and cr and machine_h1:
        body = strip_duplicate_lead_title_from_body_html(body, machine_h1)
    if site["key"] == "cd":
        body = cd_resolve_gdoc_footnote_images(body)
        body = cd_strip_residual_footnote_url_paragraphs(body)
        if client_src:
            body = cd_relocate_lead_images_after_substantive_opening(
                body, used_client_hero=True
            )
    body = canonicalize_body_http_links_cd(site, body)
    body = normalize_cd_body_support_links_for_dofollow(site, body)
    if site["key"] == "cd":
        body = cd_deduplicate_inline_body_images(
            body, hero_src_to_skip=(client_src or "").strip()
        )
        body = cd_reupload_inline_body_images(
            site,
            body,
            topic_slug=topic,
            post_title=title,
            hero_src_to_skip=(client_src or "").strip(),
        )
        body = cd_promote_gdoc_heading_paragraphs(body)
        body = cd_format_body_inline_images(body, post_title=title, site=site)
        body = format_to_audit_standard(body, site=site)
        cd_sync_inline_attachment_alts_from_body(site, body)
    if site["key"] == "cd":
        focus = refine_focus_keyword_for_content(
            focus, body=body, doc_html=ghtml, title=title, topic_slug=topic
        )

    print(f"[2] Title: {title}")
    used_client_hero = False
    pexels_used_query = ""
    client_unknown_credit = False

    if client_src:
        print(f"[3] Client image from Doc — using as hero/social (CRITICAL_RULES); Pexels hero skipped")
        pil = _pil_image_from_src(client_src)
        prov_url, prov_label, prov_flags = attempt_image_provenance(pil)
        manual_flags.extend(prov_flags)
        hero_img, social_img = build_resized_pair_from_pil(site, pil)
        p_name = prov_label
        p_profile = prov_url or "https://www.pexels.com/"
        cite = client_photo_citation_html(prov_url, prov_label)
        client_unknown_credit = not prov_url and not (prov_label or "").strip()
        cap = (
            ""
            if client_unknown_credit
            else (f"Photo: {(prov_label or '').strip()}".strip() or "Photo")
        )
        used_client_hero = True
    else:
        print(f"[3] Pexels search: {hero_q!r}")
        hero_pick, pexels_used_query = resolve_hero_pexels_photo(site, title, topic, hero_q)
        if pexels_used_query != hero_q:
            print(f"[3b] Pexels fallback query succeeded: {pexels_used_query!r}")
        p_name, p_profile, _p_page = photographer_meta(hero_pick)
        fb = (plan.get("photographer_fallback_name") or "").strip()
        if fb:
            p_name = fb
        hero_img, social_img = build_resized_pair(site, hero_pick)
        cite = (
            f'<p><em><a href="{p_profile}" target="_blank" rel="nofollow noopener">'
            f"Photo: {p_name} via Pexels</a></em></p>"
        )
        cap = f"Photo: {p_name} via Pexels"

    assert hero_img.size == (site["hero_w"], site["hero_h"]), hero_img.size
    assert social_img.size == (site["social_w"], site["social_h"]), social_img.size

    prefix = site["prefix"]
    slug = topic
    planner_img_alt = (plan.get("hero_image_alt") or "").strip()
    if site["key"] == "cd":
        alt = hero_social_alt_for_cd(
            planner_alt=planner_img_alt,
            post_title=title,
            used_client_hero=used_client_hero,
            hero_pexels_query=hero_q,
            topic_slug=topic,
        )
    elif cr:
        alt = f"Sponsored article banner image for {topic.replace('-', ' ')}"
    else:
        alt = f"{title} — banner image highlighting the story's subject matter."

    hero_fn = f"{prefix}-{slug}-hero.jpg"
    social_fn = f"{prefix}-{slug}-social.jpg"

    print(f"[4] Uploading hero {hero_fn}…")
    hero_media = wp_upload_jpeg(
        site, hero_img, hero_fn, f"{prefix}-{slug}-hero", alt, cap
    )
    hero_id = int(hero_media["id"])
    hero_url = hero_media.get("source_url") or ""

    print(f"[5] Uploading social {social_fn}…")
    wp_u, auth_u = wp_auth(site)
    social_media = wp_upload_jpeg(
        site, social_img, social_fn, f"{prefix}-{slug}-social", alt, cap
    )
    social_id = int(social_media["id"])
    r_sv = requests.get(
        f"{wp_u}/wp-json/wp/v2/media/{social_id}?context=edit",
        auth=auth_u,
        timeout=30,
    )
    r_sv.raise_for_status()
    assert_cd_social_attachment_stored_dimensions(
        site, r_sv.json(), context="pipeline social upload"
    )
    social_media = r_sv.json()
    social_url = social_media.get("source_url") or ""

    cite_html = (cite or "").strip()
    if cite_html:
        tail = "<!--scoutmonkeys-machine-tail-->\n" + cite_html + "\n<hr />\n" + donation_html_for(site)
    else:
        tail = "<!--scoutmonkeys-machine-tail-->\n<hr />\n" + donation_html_for(site)
    content = body.rstrip() + "\n\n" + tail

    seo = {
        "focus_keyword": focus,
        "seo_title": seo_title,
        "meta_description": meta,
        "excerpt": meta,
    }

    if cr and site["key"] == "cd":
        cat_id, cat_note = resolve_cd_sponsored_category(
            site, category_hint=cat_hint, topic_slug=topic, title=title
        )
        print(f"[6] WordPress category id={cat_id} ({cat_note})")
    else:
        cat_id = resolve_default_category(site, cat_hint)

    post_title = title
    if not cr and len(post_title) > site["title_max"]:
        post_title = post_title[: site["title_max"] - 1].rstrip() + "…"
        print(f"[6b] Post title trimmed to {site['title_max']} chars")

    print(f"[7] Creating WordPress draft…")
    post = create_wp_draft(
        site=site,
        title=post_title,
        content=content,
        seo=seo,
        category_id=cat_id,
        hero_id=hero_id,
        social_url=social_url,
        excerpt=seo["excerpt"],
    )
    post_id = int(post["id"])
    edit_url = f"{site['wp_url']}/wp-admin/post.php?post={post_id}&action=edit"
    print(f"[8] Draft id={post_id} url={edit_url}")

    print(f"[9] Updating AIOSEO + cd-seo…")
    push_aioseo_and_cdseo(
        site,
        post_id,
        seo,
        social_url,
        seo_title_max=(60 if site["key"] == "cd" else (500 if cr else None)),
    )

    sid = int(social_id)

    print(f"[9b] Running verify_post…")
    qa_ok = verify_post(
        site,
        post_id,
        seo,
        hero_id,
        sid,
        site["title_max"],
        expect_exact_title=machine_h1 if cr and machine_h1 else None,
        critical_rules=cr,
    )

    print(f"[10] WhatsApp notification…")
    send_whatsapp(post_id, post_title, edit_url, site["site_label"], qa_ok=qa_ok)

    return {
        "post_id": post_id,
        "edit_url": edit_url,
        "hero_id": hero_id,
        "social_id": sid,
        "qa_ok": qa_ok,
        "title": post_title,
        "hero_url": hero_url,
        "social_url": social_url,
        "intake_summary": intake.get("summary"),
        "intake_contract_flags": intake.get("contract_flags"),
        "critical_rules_active": cr,
        "used_client_hero": used_client_hero,
        "manual_review_flags": manual_flags,
    }


def main(argv: List[str]) -> None:
    if len(argv) < 1:
        print(
            "Usage: python pipeline.py <google-doc-url> [cd|dcr]\n"
            "       python pipeline.py remediate-latest cd",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if argv[0] == "remediate-latest":
        site_key = argv[1] if len(argv) > 1 else "cd"
        if site_key != "cd":
            print("remediate-latest is only supported for cd", file=sys.stderr)
            raise SystemExit(2)
        out = remediate_latest_cd_draft()
        print(json.dumps(out, indent=2))
        raise SystemExit(0 if out.get("qa_ok") else 1)
    url = argv[0]
    site = argv[1] if len(argv) > 1 else "cd"
    out = run(url, site)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
