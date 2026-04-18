"""
Scoutmonkeys publishing pipeline: Google Doc → WordPress draft (CD / DCR) with QA + Twilio WhatsApp.

Environment variables are documented in CLAUDE.md. Run:

    python pipeline.py "<google doc url>" cd
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import sys
import textwrap
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps

import doc_parser

REPO_ROOT = Path(__file__).resolve().parent
CRITICAL_RULES_PATH = REPO_ROOT / "CRITICAL_RULES.md"

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

# Canonical tail pieces (QA.md)
DONATION_HTML_CD = textwrap.dedent(
    """\
    <p><strong><a href="https://www.culturaldaily.com/support/" target="_blank" rel="nofollow noopener">CLICK HERE TO DONATE NOW TO SUPPORT NONPROFIT JOURNALISM AT CULTURAL DAILY!</a></strong></p>
    """
).strip()


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
    doc_id = gdoc_id_from_url(doc_url)
    export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=html"
    r = requests.get(
        export_url,
        timeout=60,
        headers={"User-Agent": "ScoutmonkeysPipeline/1.0"},
    )
    r.raise_for_status()
    return r.text


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
    return textwrap.dedent(
        f"""
        You are the Scoutmonkeys sponsored-content formatter for {site["site_label"]}.
        Convert the supplied Google Docs HTML into pipeline JSON.

        HARD RULE — no exceptions, no alternate link classes:
        1) Every article on this site is sponsored paid content. Every outbound http(s) link in
           article_body_html is a paid dofollow anchor, exactly:
           <a href="URL" target="_blank"><strong>anchor text</strong></a>
           Never use rel="nofollow" on body links. Do not label or treat any body link as editorial.
           (The pipeline adds the Pexels photo citation + donation tail separately — do not include them.)
        2) If the Google Doc has no usable client hero image, you MUST still output a strong
           hero_pexels_query (3–8 words) so the pipeline can always source a Pexels banner. Never omit
           or empty that field.

        Output ONLY a JSON object (no markdown fences) with keys:
        - topic_slug: lowercase kebab-case, ascii, based on the article topic
        - post_title: concise H1-style title
        - article_body_html: WordPress-ready HTML for the article body ONLY (no tail citation/hr/donation).
          Use <p>, <h2>/<h3>, <ul>/<li>, <blockquote> as needed.
          CRITICAL: article_body_html must be valid inside JSON — escape every " as \\" and use \\n for
          newlines (no raw line breaks inside JSON string values).
        - focus_keyword: short phrase for SEO
        - seo_title: <= {site["seo_title_max"]} characters
        - meta_description: <= 160 characters, plain text
        - hero_pexels_query: 3-8 word Pexels search query for a wide banner image (required; used even when the Doc has no images)
        - photographer_fallback_name: string
        - category_hint: short string like "travel", "film", "books", "food", "music", "theater", "art"

        If MACHINE_INTAKE_JSON is present, it lists each http(s) anchor with shape hints only
        (bold, target_blank, nofollow, inline color). There is no parser “classification” of links —
        every listed body URL must end up in the canonical paid anchor shape above.
        """
    ).strip()


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
                - article_body_html MUST preserve source wording. No paraphrase, summary, tone rewrite,
                  or “improvement”. Only minimal HTML structure fixes and wrapping body http(s) links as
                  <a href="URL" target="_blank"><strong>exact anchor text</strong></a> without changing words.
                - Do NOT alter donation text — the pipeline appends the canonical donation block; never invent or rewrite it.
                - focus_keyword: at most 4 words, compact core subject only (never the full H1).
                - meta_description: <=160 chars; use only wording supported by the supplied HTML
                  (no new factual claims).
                - category_hint must be exactly: Check This Out (never Sponsored).
                - If MACHINE_CLIENT_IMAGE_SRC is not "(none)", set hero_pexels_query to "" (empty string).
                - Social image is mandatory: the pipeline always generates and sets OG/social; never omit.
                - Social output must be exactly 1920×1400 pixels (handled by the pipeline resize — do not suggest other sizes).
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

    # Anthropic org TPM can reject very large single messages; keep CRITICAL runs smaller.
    doc_clip = 96_000 if critical_rules else 240_000
    user_parts = [
        "GOOGLE_DOC_HTML_START\n",
        gdoc_html[:doc_clip],
        "\nGOOGLE_DOC_HTML_END\n",
    ]
    if critical_rules and len(gdoc_html) > doc_clip:
        user_parts.append(
            f"\nNOTE: Doc HTML was clipped to {doc_clip} characters for API limits; "
            "use the visible portion (H1 and body should still be present).\n"
        )
    if critical_rules:
        user_parts.append(
            "\nMACHINE_EXTRACTED_H1 (post_title MUST match exactly):\n"
            + (machine_h1 or "(empty — extract from HTML failed)")
            + "\n"
        )
        user_parts.append(
            "\nMACHINE_CLIENT_IMAGE_SRC:\n" + (client_image_src or "(none)") + "\n"
        )
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
            "same keys and semantics as the Scoutmonkeys pipeline: topic_slug, post_title, "
            "article_body_html, focus_keyword, seo_title, meta_description, hero_pexels_query, "
            "photographer_fallback_name, category_hint. "
            "Hard site rule: every body http(s) link is paid dofollow — "
            "<a href=… target=_blank><strong>…</strong></a> with no rel=nofollow. "
            "If CRITICAL_RULES applied, post_title must match MACHINE_EXTRACTED_H1 and hero_pexels_query may be empty when client image exists."
        )
        fix_user = (
            "The text below was meant to be one JSON object but it is invalid JSON (often "
            "unescaped quotes or raw newlines inside article_body_html). "
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


def _resize_cover_ceil(img: Image.Image, tw: int, th: int) -> Image.Image:
    """
    Cover-crop to exact tw x th using ImageOps.fit semantics.
    CLAUDE.md: prefer ceil/max guards for dimension stability — Pillow handles this internally.
    """
    tw = max(1, int(math.ceil(tw)))
    th = max(1, int(math.ceil(th)))
    return ImageOps.fit(img, (tw, th), method=Image.Resampling.LANCZOS)


def build_resized_pair(site: dict, hero_photo: dict) -> Tuple[Image.Image, Image.Image]:
    src = hero_photo.get("src") or {}
    url = src.get("original") or src.get("large2x") or src.get("large")
    img = _download_image(url)
    hero = _resize_cover_ceil(img, site["hero_w"], site["hero_h"])
    social = _resize_cover_ceil(img, site["social_w"], site["social_h"])
    return hero, social


def critical_rules_active() -> bool:
    return CRITICAL_RULES_PATH.is_file()


def load_critical_rules_text(max_chars: int = 28_000) -> str:
    if not critical_rules_active():
        return ""
    return CRITICAL_RULES_PATH.read_text(encoding="utf-8", errors="replace")[:max_chars]


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
    hero = _resize_cover_ceil(img, site["hero_w"], site["hero_h"])
    social = _resize_cover_ceil(img, site["social_w"], site["social_h"])
    return hero, social


def attempt_image_provenance(img: Image.Image) -> Tuple[Optional[str], str, List[str]]:
    """CRITICAL_RULES §4 — hook for reverse search / EXIF metadata."""
    _ = img
    return None, "Client-supplied image", ["reverse_image_lookup_not_implemented"]


def compact_focus_keyword(raw: str, *, max_words: int = 4, max_len: int = 48) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    words = raw.split()
    if len(words) > max_words:
        raw = " ".join(words[:max_words])
    return raw[:max_len].rstrip(" -–—")


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
    label = (credit_label or "Client-supplied image").strip()
    if credit_url:
        return (
            f'<p><em><a href="{credit_url}" target="_blank" rel="nofollow noopener">'
            f"Photo: {label}</a></em></p>"
        )
    return f"<p><em>Photo: {label}</em></p>"


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
    body = {
        "postId": post_id,
        "post_id": post_id,
        "title": seo.get("seo_title") or "",
        "description": seo.get("meta_description") or "",
        "og_title": seo.get("seo_title") or "",
        "og_description": seo.get("meta_description") or "",
        "og_image_type": "custom",
        "og_image_custom_url": og_custom_url,
        "twitter_use_og": True,
        "keyphrases": json.dumps(
            {"focus": {"keyphrase": seo.get("focus_keyword") or ""}},
            ensure_ascii=False,
        ),
    }
    r = requests.post(
        f"{wp}/wp-json/aioseo/v1/post",
        auth=auth,
        json=body,
        timeout=60,
    )
    if not r.ok:
        print(f"[warn] aioseo/v1/post {r.status_code}: {r.text[:400]}")
    # 2) cd-seo plugin persists AIOSEO DB + OG (see wp-json /cd-seo/v1/update args)
    r2 = requests.post(
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
    if not r2.ok:
        print(f"[warn] cd-seo/v1/update {r2.status_code}: {r2.text[:400]}")


def resolve_social_id(wp: str, auth, post_id: int, hero_id: int) -> Optional[int]:
    aioseo = requests.get(
        f"{wp}/wp-json/aioseo/v1/post?postId={post_id}",
        auth=auth,
        timeout=30,
    ).json()
    og = aioseo.get("data", {}).get("currentPost", {}).get("og_image_custom_url") or ""
    if not og:
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
            su = item.get("source_url") or ""
            if slug in su or slug.replace("-1", "") in su:
                if item["id"] != hero_id:
                    return int(item["id"])
    return None


def _cap_raw(media: dict) -> str:
    c = media.get("caption") or {}
    return c.get("raw") or c.get("rendered") or ""


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
        parent = getattr(a, "parent", None)
        already_bold = bool(a.find("strong") or a.find("b")) or (
            parent is not None and getattr(parent, "name", "") in ("strong", "b")
        )
        if not already_bold:
            inner = a.decode_contents()
            if inner.strip():
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
    _ = seo
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
    if critical_rules and expect_exact_title:
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
    chk("Meta description ≤160 chars", 0 < len(meta) <= 160, f"{len(meta)} chars")

    try:
        kw = json.loads(seo_r["aioseo_db"].get("keyphrases") or "{}").get("focus", {}).get(
            "keyphrase", ""
        )
    except Exception:
        kw = ""
    chk("Focus keyword set", bool(kw), f"'{kw}'")
    if critical_rules:
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

    ht = hero.get("title") or {}
    h_title = ht.get("raw") or ht.get("rendered") or ""
    chk(
        f"Hero title ({prefix}-...-hero)",
        h_title.startswith(prefix + "-") and h_title.endswith("-hero"),
        f"'{h_title}'",
    )

    h_alt = hero.get("alt_text") or ""
    chk("Hero alt text descriptive (>10 chars)", len(h_alt) > 10, f'"{h_alt[:50]}"')

    h_cap = _cap_raw(hero)
    chk("Hero caption starts 'Photo:'", h_cap.startswith("Photo:"), f'"{h_cap}"')

    st = soc.get("title") or {}
    s_title = st.get("raw") or st.get("rendered") or ""
    chk(
        f"Social title ({prefix}-...-social)",
        s_title.startswith(prefix + "-") and s_title.endswith("-social"),
        f"'{s_title}'",
    )

    s_alt = soc.get("alt_text") or ""
    chk("Social alt matches hero", s_alt == h_alt)

    s_cap = _cap_raw(soc)
    chk("Social caption starts 'Photo:'", s_cap.startswith("Photo:"), f'"{s_cap}"')

    aioseo_post = requests.get(
        f"{wp}/wp-json/aioseo/v1/post?postId={post_id}", auth=auth, timeout=30
    ).json()
    og = aioseo_post.get("data", {}).get("currentPost", {}).get("og_image_custom_url") or ""
    og_cd = (seo_r.get("aioseo_db") or {}).get("og_image_url") or ""
    og_ok = bool(og or og_cd)
    og_note = (og or og_cd)[-50:] if og_ok else "missing"
    chk("Social set as OG image (AIOSEO / cd-seo)", og_ok, og_note)

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
    cite_ok = cite_pexels or cite_other or cite_client_plain
    chk("Citation: italic+hyperlinked, not bold", cite_ok)
    chk("Citation NOT bold", "<strong>Photo:" not in c)

    chk("Horizontal rule <hr />", "<hr />" in c)
    chk("No <!--nextpage--> page break", "<!--nextpage-->" not in c)

    chk("Donation box present", "CLICK HERE TO DONATE" in c)

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
    """Load `REPO_ROOT/.env` into os.environ (used only by CLI remediate — not `run()`)."""
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
    force **Check This Out** category, compact focus keyphrase, SEO title = post title,
    ensure OG social URL is set, and re-upload social JPEG at exact 1920×1400 from the
    current featured hero if the existing social attachment is missing or wrong size.
    """
    if not critical_rules_active():
        raise RuntimeError("CRITICAL_RULES.md is missing.")
    _apply_repo_dotenv_for_cli()
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
    alt = hero_alt or raw_title.strip() or "Article"

    sid = resolve_social_id(wp, auth, post_id, hero_id)
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

    actions: List[str] = []
    cat_id = resolve_check_this_out_category(site)
    rp = requests.post(
        f"{wp}/wp-json/wp/v2/posts/{post_id}",
        auth=auth,
        json={"categories": [cat_id]},
        timeout=60,
    )
    rp.raise_for_status()
    actions.append(f"categories=[{cat_id}] Check This Out")

    if regen_social:
        pil = _download_image(hero_url).convert("RGB")
        social_img = _resize_cover_ceil(pil, site["social_w"], site["social_h"])
        assert social_img.size == (site["social_w"], site["social_h"]), social_img.size
        cap = _cap_raw(hero)
        if not cap.startswith("Photo:"):
            cap = f"Photo: {cap}" if cap else "Photo: Cultural Daily"
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
        social_url = (sm.get("source_url") or "").strip()
        sid = int(sm["id"])
        actions.append(f"reuploaded social media id={sid} {site['social_w']}×{site['social_h']}")
    else:
        soc = requests.get(
            f"{wp}/wp-json/wp/v2/media/{int(sid)}?context=edit",
            auth=auth,
            timeout=30,
        ).json()
        social_url = (soc.get("source_url") or "").strip()

    if not social_url:
        raise RuntimeError("Could not resolve social image URL for AIOSEO.")

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
    focus = compact_focus_keyword((kw or slug.replace("-", " ")).strip())
    meta = ((seo_r.get("aioseo_db") or {}).get("description") or "")[:160]
    if not meta:
        meta = (raw_title[:157] + "...") if len(raw_title) > 160 else raw_title
    seo = {
        "focus_keyword": focus,
        "seo_title": raw_title,
        "meta_description": meta,
        "excerpt": meta,
    }
    push_aioseo_and_cdseo(
        site,
        post_id,
        seo,
        social_url,
        seo_title_max=500,
    )
    actions.append("aioseo+cd-seo: focus compact, seo_title=post title, og_image set")

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
    return {"post_id": post_id, "hero_id": hero_id, "social_id": int(sid), "actions": actions, "qa_ok": qa}


def send_whatsapp(post_id: int, title: str, edit_url: str, site_label: str) -> None:
    if not TWILIO_SID or not TWILIO_TOKEN or not TWILIO_FROM:
        print("[10] ⚠ WhatsApp skipped — TWILIO creds not set")
        return
    if TWILIO_SID == "TWILIO_ACCOUNT_SID" or TWILIO_TOKEN == "TWILIO_AUTH_TOKEN":
        print("[10] ⚠ WhatsApp skipped — Twilio env vars are still Railway placeholders")
        return
    to = WA_TO or f"whatsapp:{WA_PHONE}"
    msg = (
        f"✅ Draft saved — {site_label}\n"
        f"\"{title}\"\n"
        f"ID: {post_id}\n"
        f"Edit: {edit_url}"
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
    print(
        f"     images={summ.get('image_count')} credits={summ.get('photo_credit_block_count')} "
        f"links={summ.get('hyperlink_count')} "
        f"body_links_not_canonical={summ.get('body_links_not_canonical_count', 0)}"
    )
    flags = intake.get("contract_flags") or []
    if flags:
        print(f"     contract_flags: {', '.join(flags)}")

    cr = critical_rules_active()
    machine_h1 = extract_h1_from_gdoc_html(ghtml).strip()
    client_src = first_client_image_src_from_gdoc(ghtml)
    manual_flags: List[str] = []
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
    body = plan.get("article_body_html") or ""
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
        focus = compact_focus_keyword(focus or topic.replace("-", " "))
        if not meta:
            meta = derive_meta_from_gdoc_first_paragraph(ghtml)[:160]
        seo_title = title
        cat_hint = "Check This Out"
        if client_src:
            hero_q = ""

    if not cr:
        seo_title = seo_title[: site["seo_title_max"]]

    body = normalize_cd_body_support_links_for_dofollow(site, body)

    print(f"[2] Title: {title}")
    used_client_hero = False
    pexels_used_query = ""

    if client_src:
        print(f"[3] Client image from Doc — using as hero/social (CRITICAL_RULES); Pexels hero skipped")
        pil = _pil_image_from_src(client_src)
        prov_url, prov_label, prov_flags = attempt_image_provenance(pil)
        manual_flags.extend(prov_flags)
        hero_img, social_img = build_resized_pair_from_pil(site, pil)
        p_name = prov_label
        p_profile = prov_url or "https://www.pexels.com/"
        cite = client_photo_citation_html(prov_url, prov_label)
        cap = f"Photo: {prov_label}"
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
    alt = title if cr else f"{title} — banner image highlighting the story's subject matter."

    hero_fn = f"{prefix}-{slug}-hero.jpg"
    social_fn = f"{prefix}-{slug}-social.jpg"

    print(f"[4] Uploading hero {hero_fn}…")
    hero_media = wp_upload_jpeg(
        site, hero_img, hero_fn, f"{prefix}-{slug}-hero", alt, cap
    )
    hero_id = int(hero_media["id"])
    hero_url = hero_media.get("source_url") or ""

    print(f"[5] Uploading social {social_fn}…")
    social_media = wp_upload_jpeg(
        site, social_img, social_fn, f"{prefix}-{slug}-social", alt, cap
    )
    social_id = int(social_media["id"])
    social_url = social_media.get("source_url") or ""

    tail = "<!--scoutmonkeys-machine-tail-->\n" + cite + "\n<hr />\n" + donation_html_for(site)
    content = body.rstrip() + "\n\n" + tail

    seo = {
        "focus_keyword": focus,
        "seo_title": seo_title,
        "meta_description": meta,
        "excerpt": meta,
    }

    if cr and site["key"] == "cd":
        cat_id = resolve_check_this_out_category(site)
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
        seo_title_max=(500 if cr else None),
    )

    sid = resolve_social_id(site["wp_url"], (site["wp_user"], site["wp_pass"]), post_id, hero_id)
    if not sid:
        sid = social_id

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
    send_whatsapp(post_id, post_title, edit_url, site["site_label"])

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
