#!/usr/bin/env python3
"""
scripts/build_matched_pairs.py

For each Google Doc in data/training_docs.txt, find the matching Cultural Daily
WordPress post (by H1 title), fetch both, and record what actually happened:
- Where each doc image ended up (hero, body at exact position, or dropped)
- Final SEO title vs doc H1
- Focus keyword chosen
- Category assigned
- Donation CTA presence and position

Output: data/matched_pairs.json  — the pipeline's training reference.

Usage:
    python3 scripts/build_matched_pairs.py
    LIMIT=10 python3 scripts/build_matched_pairs.py   # first N docs only
    DELAY=1.0 python3 scripts/build_matched_pairs.py  # slower (polite)
"""
from __future__ import annotations

import html as html_module
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parent.parent
TRAINING_DOCS = REPO_ROOT / "data" / "training_docs.txt"
OUT_JSON = REPO_ROOT / "data" / "matched_pairs.json"


# ── env loading ───────────────────────────────────────────────────────────────

def _load_env() -> None:
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
        if k not in os.environ:
            os.environ[k] = v


_load_env()

WP_URL = os.environ.get("WP_URL", "https://www.culturaldaily.com").rstrip("/")
WP_USER = os.environ.get("WP_USER", "")
WP_PASS = os.environ.get("WP_PASS", "")
AUTHOR_ID = int(os.environ.get("OUR_FRIENDS_AUTHOR_ID", "19"))
AUTH = (WP_USER, WP_PASS)
DELAY = float(os.environ.get("DELAY", "0.5"))
LIMIT = int(os.environ.get("LIMIT", "0")) or None


# ── Google Doc helpers ────────────────────────────────────────────────────────

def _gdoc_export_url(gdoc_url: str) -> str:
    m = re.search(r"/document/d/([a-zA-Z0-9_-]+)", gdoc_url)
    if not m:
        raise ValueError(f"Cannot extract doc ID from: {gdoc_url}")
    return f"https://docs.google.com/document/d/{m.group(1)}/export?format=html"


def fetch_gdoc_html(gdoc_url: str) -> Optional[str]:
    try:
        r = requests.get(_gdoc_export_url(gdoc_url), timeout=30, allow_redirects=True)
        if r.status_code in (401, 403, 410):
            print(f"    [skip] Doc not public ({r.status_code})")
            return None
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"    [warn] Doc fetch failed: {e}")
        return None


def parse_gdoc(ghtml: str) -> dict:
    """
    Extract H1, hero image (first img after H1, before first H2), and body images
    with the heading they sit under, from Google Doc export HTML.
    """
    soup = BeautifulSoup(ghtml, "html.parser")

    # H1 / title element
    h1_el = soup.find("h1")
    if h1_el is None:
        for p in soup.find_all("p"):
            cls = " ".join(p.get("class") or []).lower()
            if "title" in cls and p.get_text(strip=True):
                h1_el = p
                break
    h1_text = h1_el.get_text(" ", strip=True) if h1_el else ""

    HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    SECTION_HEADINGS = {"h2", "h3", "h4", "h5", "h6"}

    hero: Optional[dict] = None
    body_images: list[dict] = []
    total_images = 0

    found_h1 = False
    after_first_section_heading = False
    current_heading = ""

    for el in soup.descendants:
        if not hasattr(el, "name") or not el.name:
            continue
        if el is h1_el:
            found_h1 = True
            continue
        if not found_h1:
            continue
        if el.name in SECTION_HEADINGS:
            after_first_section_heading = True
            current_heading = el.get_text(" ", strip=True)
            continue
        if el.name == "img":
            src = (el.get("src") or "").strip()
            if not src or src.startswith("blob:"):
                continue
            if not src.startswith(("http", "data:")):
                continue
            total_images += 1
            rec = {
                "src_type": "data:" if src.startswith("data:") else "http",
                "preceding_heading": current_heading,
            }
            if not after_first_section_heading and hero is None:
                hero = rec
            else:
                body_images.append({**rec, "body_index": len(body_images) + 1})

    return {
        "h1": h1_text,
        "total_images": total_images,
        "hero_image": hero,
        "body_images": body_images,
    }


# ── WordPress helpers ─────────────────────────────────────────────────────────

def _clean_wp_title(raw: dict) -> str:
    t = (raw.get("raw") or raw.get("rendered") or "").strip()
    return html_module.unescape(re.sub(r"<[^>]+>", "", t)).strip()


def wp_find_post_by_title(title: str) -> Optional[dict]:
    if not title:
        return None
    title_l = title.lower().strip()
    # NOTE: ?author=X returns HTTP 500 on Cultural Daily — never pass it in search params.
    # Filter by author after receiving results instead.
    for status in ("publish", "draft", "any"):
        try:
            r = requests.get(
                f"{WP_URL}/wp-json/wp/v2/posts",
                auth=AUTH,
                params={"search": title[:100], "per_page": 20, "status": status},
                timeout=30,
            )
            if not r.ok:
                continue
            rows = r.json()
            # Prefer author-exact match; fall back to any author for the same title
            author_match = None
            for row in rows:
                t = _clean_wp_title(row.get("title") or {}).lower()
                if t == title_l:
                    if row.get("author") == AUTHOR_ID:
                        return row
                    if author_match is None:
                        author_match = row
            if author_match:
                return author_match
        except Exception:
            continue
    return None


def wp_get_post_full(post_id: int) -> Optional[dict]:
    try:
        r = requests.get(
            f"{WP_URL}/wp-json/wp/v2/posts/{post_id}?context=edit",
            auth=AUTH, timeout=30,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"    [warn] WP post fetch failed: {e}")
        return None


def wp_get_media(media_id: int) -> dict:
    try:
        r = requests.get(
            f"{WP_URL}/wp-json/wp/v2/media/{media_id}?context=edit",
            auth=AUTH, timeout=20,
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}


def wp_get_aioseo(post_id: int) -> dict:
    try:
        r = requests.get(
            f"{WP_URL}/wp-json/aioseo/v1/post?postId={post_id}",
            auth=AUTH, timeout=20,
        )
        if not r.ok:
            return {}
        curp = (r.json().get("data") or {}).get("currentPost") or {}
        return curp
    except Exception:
        return {}


def wp_get_category_slugs(cat_ids: list) -> list:
    slugs = []
    for cid in cat_ids:
        try:
            r = requests.get(f"{WP_URL}/wp-json/wp/v2/categories/{cid}",
                             auth=AUTH, timeout=15)
            if r.ok:
                slugs.append(r.json().get("slug", str(cid)))
        except Exception:
            slugs.append(str(cid))
        time.sleep(0.15)
    return slugs


def extract_gdoc_url_from_wp_content(content_raw: str) -> str:
    """Extract the source Google Doc URL stored by the pipeline as an HTML comment."""
    m = re.search(r"<!--\s*scoutmonkeys-gdoc:(https://[^\s>]+)\s*-->", content_raw or "")
    return m.group(1).strip() if m else ""


def parse_wp_body(content_raw: str) -> dict:
    """Extract body images with their preceding headings, plus structural markers."""
    soup = BeautifulSoup(content_raw or "", "html.parser")
    body_images = []
    current_heading = ""

    for el in soup.descendants:
        if not hasattr(el, "name") or not el.name:
            continue
        if el.name in ("h2", "h3", "h4", "h5", "h6"):
            current_heading = el.get_text(" ", strip=True)
        elif el.name == "img":
            src = (el.get("src") or "").strip()
            if not src:
                continue
            classes = el.get("class") or []
            if isinstance(classes, str):
                classes = classes.split()
            mid = None
            for c in classes:
                if isinstance(c, str) and c.startswith("wp-image-"):
                    try:
                        mid = int(c.replace("wp-image-", "", 1))
                    except ValueError:
                        pass
            body_images.append({
                "wp_media_id": mid,
                "src_snippet": src[:120],
                "preceding_heading": current_heading,
                "alt_snippet": (el.get("alt") or "")[:120],
                "body_index": len(body_images) + 1,
            })

    text = soup.get_text(" ")
    has_donation = "CLICK HERE TO DONATE" in text.upper()
    has_hr = bool(soup.find("hr"))
    donation_offset = text.upper().find("CLICK HERE TO DONATE")
    char_len = len(text)

    return {
        "body_images": body_images,
        "has_donation": has_donation,
        "has_hr": has_hr,
        "donation_offset_chars": donation_offset,
        "content_char_len": char_len,
    }


def analyze_wp_post(post: dict) -> dict:
    post_id = post["id"]
    content_raw = (post.get("content") or {}).get("raw") or ""
    source_gdoc_url = extract_gdoc_url_from_wp_content(content_raw)

    # Hero / featured image
    hero_id = int(post.get("featured_media") or 0)
    hero_info: dict = {}
    if hero_id:
        m = wp_get_media(hero_id)
        if m:
            md = m.get("media_details") or {}
            hero_info = {
                "media_id": hero_id,
                "width": md.get("width", 0),
                "height": md.get("height", 0),
                "title": ((m.get("title") or {}).get("raw") or "").strip(),
                "alt_len": len((m.get("alt_text") or "")),
                "source_url": (m.get("source_url") or ""),
            }
        time.sleep(DELAY * 0.5)

    # AIOSEO
    aioseo = wp_get_aioseo(post_id)
    time.sleep(DELAY * 0.5)

    seo_title = (aioseo.get("title") or "").strip()
    focus_kw = ""
    kp_raw = aioseo.get("keyphrases") or ""
    if kp_raw:
        try:
            kp = json.loads(kp_raw) if isinstance(kp_raw, str) else kp_raw
            focus_kw = ((kp.get("focus") or {}).get("keyphrase") or "").strip()
        except Exception:
            pass
    meta_desc = (aioseo.get("description") or "").strip()

    # Body
    body_data = parse_wp_body(content_raw)

    # Categories
    cat_ids = post.get("categories") or []
    cat_slugs = wp_get_category_slugs(cat_ids)

    return {
        "title": _clean_wp_title(post.get("title") or {}),
        "source_gdoc_url_in_content": source_gdoc_url,
        "status": post.get("status", ""),
        "date": post.get("date", ""),
        "hero": hero_info,
        "body_image_count": len(body_data["body_images"]),
        "body_images": body_data["body_images"],
        "seo_title": seo_title,
        "seo_title_len": len(seo_title),
        "focus_keyword": focus_kw,
        "meta_description_len": len(meta_desc),
        "category_slugs": cat_slugs,
        "has_donation": body_data["has_donation"],
        "has_hr": body_data["has_hr"],
        "donation_offset_chars": body_data["donation_offset_chars"],
        "content_char_len": body_data["content_char_len"],
    }


def build_comparison(doc: dict, wp: dict) -> dict:
    h1 = doc.get("h1", "")
    wp_title = wp.get("title", "")
    title_exact = h1.lower().strip() == wp_title.lower().strip()
    title_truncated = (not title_exact) and wp_title and h1.lower().startswith(wp_title.lower()[:40])

    hero_in_doc = doc.get("hero_image") is not None
    hero_in_wp = bool(wp.get("hero") and wp["hero"].get("media_id"))
    hero_correct_size = (
        wp["hero"].get("width") == 975 and wp["hero"].get("height") == 250
    ) if hero_in_wp else False

    doc_body_count = len(doc.get("body_images", []))
    wp_body_count = wp.get("body_image_count", 0)

    # Check heading alignment: do body images sit under the same headings in both doc and WP?
    heading_matches = []
    doc_imgs = doc.get("body_images", [])
    wp_imgs = wp.get("body_images", [])
    for i, (di, wi) in enumerate(zip(doc_imgs, wp_imgs)):
        dh = (di.get("preceding_heading") or "").lower().strip()
        wh = (wi.get("preceding_heading") or "").lower().strip()
        heading_matches.append({
            "body_index": i + 1,
            "doc_heading": di.get("preceding_heading", ""),
            "wp_heading": wi.get("preceding_heading", ""),
            "headings_match": dh == wh if dh and wh else None,
        })

    return {
        "title_exact": title_exact,
        "title_truncated": title_truncated,
        "hero_present_in_doc": hero_in_doc,
        "hero_set_in_wp": hero_in_wp,
        "hero_correct_size_975x250": hero_correct_size,
        "doc_body_image_count": doc_body_count,
        "wp_body_image_count": wp_body_count,
        "body_image_count_match": doc_body_count == wp_body_count,
        "body_image_heading_alignment": heading_matches,
        "donation_present": wp.get("has_donation", False),
        "hr_present": wp.get("has_hr", False),
    }


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    raw_lines = [
        l.strip()
        for l in TRAINING_DOCS.read_text(encoding="utf-8").splitlines()
        if l.strip() and not l.strip().startswith("#")
    ]
    if LIMIT:
        raw_lines = raw_lines[:LIMIT]
    total = len(raw_lines)
    print(f"Building matched pairs for {total} Google Docs → {OUT_JSON.name}\n")

    results: list[dict] = []

    for i, gdoc_url in enumerate(raw_lines, 1):
        print(f"[{i:02d}/{total}] {gdoc_url}")

        rec: dict = {
            "gdoc_url": gdoc_url,
            "gdoc_fetch_ok": False,
            "wp_post_id": None,
            "wp_post_url": None,
            "wp_match_method": None,
            "doc": {},
            "wp": {},
            "comparison": {},
        }

        # Fetch and parse Google Doc
        ghtml = fetch_gdoc_html(gdoc_url)
        time.sleep(DELAY)
        if not ghtml:
            results.append(rec)
            continue

        rec["gdoc_fetch_ok"] = True
        doc_data = parse_gdoc(ghtml)
        rec["doc"] = doc_data
        print(f"    H1: {doc_data['h1'][:70]!r}")
        hero_tag = "yes (under H1)" if doc_data["hero_image"] else "none"
        print(f"    Images: {doc_data['total_images']} total | hero={hero_tag} | body={len(doc_data['body_images'])}")

        if not doc_data["h1"]:
            print("    [skip] No H1 found in doc")
            results.append(rec)
            continue

        # Find matching WP post
        post_stub = wp_find_post_by_title(doc_data["h1"])
        time.sleep(DELAY)
        if not post_stub:
            print(f"    [no match] No WP post found for this title")
            results.append(rec)
            continue

        post_id = int(post_stub["id"])
        rec["wp_post_id"] = post_id
        rec["wp_post_url"] = post_stub.get("link", "")
        rec["wp_match_method"] = "title_exact"
        print(f"    WP post id={post_id}  status={post_stub.get('status')}  {rec['wp_post_url']}")

        # Fetch full post + all metadata
        full_post = wp_get_post_full(post_id)
        time.sleep(DELAY)
        if not full_post:
            results.append(rec)
            continue

        wp_data = analyze_wp_post(full_post)
        rec["wp"] = wp_data
        rec["comparison"] = build_comparison(doc_data, wp_data)

        print(f"    SEO title: {wp_data['seo_title'][:55]!r}  ({wp_data['seo_title_len']} chars)")
        print(f"    Focus kw:  {wp_data['focus_keyword']!r}")
        print(f"    Category:  {wp_data['category_slugs']}")
        print(f"    Body imgs: doc={rec['comparison']['doc_body_image_count']} wp={rec['comparison']['wp_body_image_count']}  match={rec['comparison']['body_image_count_match']}")
        print(f"    Hero:      in_doc={rec['comparison']['hero_present_in_doc']}  in_wp={rec['comparison']['hero_set_in_wp']}  size_ok={rec['comparison']['hero_correct_size_975x250']}")
        print(f"    Donation:  {rec['comparison']['donation_present']}  HR: {rec['comparison']['hr_present']}")

        results.append(rec)
        time.sleep(DELAY)

    # Write output
    OUT_JSON.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    matched = sum(1 for r in results if r.get("wp_post_id"))
    fetched = sum(1 for r in results if r.get("gdoc_fetch_ok"))
    title_ok = sum(1 for r in results if r.get("comparison", {}).get("title_exact"))
    hero_ok = sum(1 for r in results if r.get("comparison", {}).get("hero_set_in_wp"))
    body_ok = sum(1 for r in results if r.get("comparison", {}).get("body_image_count_match"))
    donation_ok = sum(1 for r in results if r.get("comparison", {}).get("donation_present"))

    print(f"\n{'='*60}")
    print(f"Done: {fetched}/{total} docs fetched, {matched}/{total} matched to WP posts")
    if matched:
        print(f"  Title exact:       {title_ok}/{matched}")
        print(f"  Hero set in WP:    {hero_ok}/{matched}")
        print(f"  Body img count =:  {body_ok}/{matched}")
        print(f"  Donation present:  {donation_ok}/{matched}")
    print(f"Saved → {OUT_JSON}")


if __name__ == "__main__":
    main()
