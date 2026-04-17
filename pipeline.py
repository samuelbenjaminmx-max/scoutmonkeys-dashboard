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
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageOps

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
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
    m = re.search(r"/document/d/([a-zA-Z0-9_-]+)", url)
    if not m:
        raise ValueError(f"Could not parse Google Doc id from URL: {url}")
    return m.group(1)


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


def _anthropic_messages(system: str, user: str) -> str:
    if not ANTHROPIC_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 12000,
        "temperature": 0.2,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=payload,
        timeout=300,
    )
    if not r.ok:
        raise RuntimeError(f"Anthropic API error {r.status_code}: {r.text[:800]}")
    data = r.json()
    parts = data["content"][0]["text"]
    return parts


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


def plan_from_gdoc_html(site: dict, gdoc_html: str) -> dict:
    system = textwrap.dedent(
        f"""
        You are the Scoutmonkeys editorial formatter for {site["site_label"]}.
        Convert the supplied Google Docs HTML into pipeline JSON.

        Output ONLY a JSON object (no markdown fences) with keys:
        - topic_slug: lowercase kebab-case, ascii, based on the article topic
        - post_title: concise H1-style title
        - article_body_html: WordPress-ready HTML for the article body ONLY (no tail citation/hr/donation).
          Use <p>, <h2>/<h3>, <ul>/<li>, <blockquote> as needed.
          For purchased / sponsored outbound links use exactly:
          <a href="URL" target="_blank"><strong>anchor text</strong></a>
          Never add inline color styles. Never wrap the photo credit line here.
        - focus_keyword: short phrase for SEO
        - seo_title: <= {site["seo_title_max"]} characters
        - meta_description: <= 160 characters, plain text
        - hero_pexels_query: 3-6 word search query to find a wide banner image on Pexels
        - photographer_fallback_name: string
        - category_hint: short string like "travel", "film", "books", "food", "music", "theater", "art"
        """
    ).strip()

    user = (
        "GOOGLE_DOC_HTML_START\n"
        + gdoc_html[:240_000]
        + "\nGOOGLE_DOC_HTML_END\n\nReturn JSON only."
    )
    raw = _anthropic_messages(system, user)
    return _extract_json_blob(raw)


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


def push_aioseo_and_cdseo(site: dict, post_id: int, seo: dict, og_custom_url: str) -> None:
    wp, auth = wp_auth(site)
    # 1) AIOSEO custom endpoint (Cultural Daily)
    body = {
        "postId": post_id,
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
    # 2) cd-seo resolves og_image_url + postmeta parity
    r2 = requests.post(
        f"{wp}/wp-json/cd-seo/v1/update",
        auth=auth,
        json={
            "post_id": post_id,
            "og_image_custom_url": og_custom_url,
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


def verify_post(
    site: dict,
    post_id: int,
    seo: dict,
    hero_id: int,
    social_id: int,
    title_max: int,
) -> bool:
    """
    Run QA checks aligned with QA.md / CLAUDE.md.
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

    checks: List[Tuple[str, bool]] = []

    def chk(label: str, ok: bool, note: str = "") -> None:
        icon = "✅" if ok else "❌"
        print(f"  {icon} {label}" + (f"  [{note}]" if note else ""))
        checks.append((label, ok))

    raw_title = post["title"]["raw"]
    chk(f"Post title ≤{title_max} chars", len(raw_title) <= title_max, f"{len(raw_title)} chars")

    seo_title = seo_r.get("aioseo_db", {}).get("title") or ""
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
    chk("Social set as OG image (AIOSEO custom_image)", bool(og), og[-40:] if og else "missing")

    chk("Paid links bold in content", "<strong>" in c)

    cite_ok = bool(
        re.search(
            r'<p><em><a href="https://www\.pexels\.com[^"]*"[^>]*>Photo: .+ via Pexels</a></em></p>',
            c,
        )
    )
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

    print(f"[2] Planning layout with Anthropic…")
    plan = plan_from_gdoc_html(site, ghtml)
    topic = re.sub(r"[^a-z0-9-]+", "-", (plan.get("topic_slug") or "topic").lower()).strip("-")
    title = (plan.get("post_title") or "Untitled").strip()
    body = plan.get("article_body_html") or ""
    focus = (plan.get("focus_keyword") or "").strip()
    seo_title = (plan.get("seo_title") or title)[: site["seo_title_max"]]
    meta = (plan.get("meta_description") or "")[:160]
    hero_q = (plan.get("hero_pexels_query") or title).strip()
    cat_hint = (plan.get("category_hint") or "culture").strip()

    print(f"[2] Title: {title}")
    print(f"[3] Pexels search: {hero_q!r}")
    photos = pexels_search(hero_q)
    target_ratio = site["hero_w"] / site["hero_h"]
    hero_pick = _pexels_pick_hero(photos, target_ratio)
    p_name, p_profile, _p_page = photographer_meta(hero_pick)
    fb = (plan.get("photographer_fallback_name") or "").strip()
    if fb:
        p_name = fb

    hero_img, social_img = build_resized_pair(site, hero_pick)
    prefix = site["prefix"]
    slug = topic
    alt = f"{title} — banner image highlighting the story's subject matter."

    hero_fn = f"{prefix}-{slug}-hero.jpg"
    social_fn = f"{prefix}-{slug}-social.jpg"
    cap = f"Photo: {p_name} via Pexels"

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

    cite = (
        f'<p><em><a href="{p_profile}" target="_blank" rel="nofollow noopener">'
        f"Photo: {p_name} via Pexels</a></em></p>"
    )
    tail = cite + "\n<hr />\n" + donation_html_for(site)
    content = body.rstrip() + "\n\n" + tail

    seo = {
        "focus_keyword": focus,
        "seo_title": seo_title,
        "meta_description": meta,
        "excerpt": meta,
    }

    cat_id = resolve_default_category(site, cat_hint)
    post_title = title
    if len(post_title) > site["title_max"]:
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
    push_aioseo_and_cdseo(site, post_id, seo, social_url)

    sid = resolve_social_id(site["wp_url"], (site["wp_user"], site["wp_pass"]), post_id, hero_id)
    if not sid:
        sid = social_id

    print(f"[9b] Running verify_post…")
    qa_ok = verify_post(site, post_id, seo, hero_id, sid, site["title_max"])

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
    }


def main(argv: List[str]) -> None:
    if len(argv) < 1:
        print("Usage: python pipeline.py <google-doc-url> [cd|dcr]", file=sys.stderr)
        raise SystemExit(2)
    url = argv[0]
    site = argv[1] if len(argv) > 1 else "cd"
    out = run(url, site)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
