#!/usr/bin/env python3
"""
Audit published Cultural Daily posts by author ID (default: Our Friends = 19).

Cultural Daily's REST API returns HTTP 500 for ?author=19 — we paginate
status=publish and filter client-side.

Env:
  WP_URL, WP_USER, WP_PASS
  OUR_FRIENDS_AUTHOR_ID (default 19)
  AUDIT_MAX_POSTS — optional int cap after filter
  AUDIT_JSON_OUT — optional path to also write full JSON
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import requests

CD_HERO = (975, 250)


def load_env() -> None:
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]
    for env_path in candidates:
        if not env_path.exists():
            continue
        for line in env_path.read_text().splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            os.environ[k] = v.strip()
        return


def _wp_auth() -> tuple[str, str]:
    return (
        os.environ.get("WP_USER", ""),
        os.environ.get("WP_PASS", ""),
    )


def fetch_our_friends_posts(wp_url: str, author_id: int, auth: tuple[str, str]) -> list:
    posts: list = []
    page = 1
    fields = "id,author,title,content,featured_media,date,link,status"
    while True:
        r = requests.get(
            f"{wp_url}/wp-json/wp/v2/posts",
            auth=auth,
            params={
                "status": "publish",
                "per_page": 100,
                "page": page,
                "_fields": fields,
            },
            timeout=90,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        for p in batch:
            if p.get("author") == author_id:
                posts.append(p)
        total_pages = int(r.headers.get("X-WP-TotalPages", "1"))
        if page >= total_pages:
            break
        page += 1
        if page % 10 == 0:
            print(f"  …scanning list page {page}/{total_pages}", file=sys.stderr)
    return posts


def fetch_media(wp_url: str, mid: int, auth: tuple[str, str]) -> dict:
    r = requests.get(
        f"{wp_url}/wp-json/wp/v2/media/{mid}",
        auth=auth,
        params={"context": "edit"},
        timeout=30,
    )
    if r.status_code != 200:
        return {"error": r.status_code, "body": r.text[:200]}
    return r.json()


def classify_hero(width: int, height: int) -> str:
    if width == CD_HERO[0] and height == CD_HERO[1]:
        return "cd_banner_975x250"
    if width and height:
        r = width / height
        if 3.5 <= r <= 4.2:
            return f"wide_banner_like_{width}x{height}"
        if 1.2 <= r <= 1.5:
            return f"social_landscape_like_{width}x{height}"
    return f"other_{width}x{height}"


def analyze_content(html: str) -> dict:
    c = html or ""
    cite_pexels_em = bool(
        re.search(
            r'<p>\s*<em>\s*<a[^>]+href="https?://(?:www\.)?pexels\.com[^"]*"[^>]*>\s*Photo:',
            c,
            re.I,
        )
    )
    cite_pexels_plain = bool(re.search(r'pexels\.com[^"]*">\s*Photo:', c, re.I))
    cite_any_photo = bool(re.search(r"Photo:\s*.+via\s+Pexels", c, re.I))
    cite_bold = "<strong>Photo:" in c or bool(re.search(r"<strong>\s*Photo:", c, re.I))

    has_donation = "CLICK HERE TO DONATE" in c or "culturaldaily.com/support" in c
    has_hr = "<hr" in c
    has_nextpage = "<!--nextpage-->" in c
    paid_style_links = len(re.findall(r"<a[^>]+>\s*<strong>", c, re.I))
    total_a = len(re.findall(r"<a\s", c, re.I))

    last_photo = None
    for m in re.finditer(r"Photo:\s*.+via\s+Pexels", c, re.I):
        last_photo = m.start()
    hr_pos = c.find("<hr")
    don_pos = c.find("CLICK HERE TO DONATE")
    if don_pos < 0:
        don_pos = c.find("culturaldaily.com/support")

    order_ok = None
    if last_photo is not None and hr_pos >= 0 and don_pos >= 0:
        order_ok = last_photo < hr_pos < don_pos

    return {
        "cite_pexels_em_a": cite_pexels_em,
        "cite_pexels_any": cite_pexels_plain,
        "cite_any_photo": cite_any_photo,
        "cite_uses_bold": cite_bold,
        "has_donation_block": has_donation,
        "has_hr": has_hr,
        "has_nextpage": has_nextpage,
        "paid_style_link_count": paid_style_links,
        "anchor_count": total_a,
        "order_photo_hr_donation": order_ok,
        "last_photo_offset": last_photo,
        "hr_offset": hr_pos if hr_pos >= 0 else None,
        "donation_offset": don_pos if don_pos >= 0 else None,
    }


def main() -> None:
    load_env()
    wp_url = os.environ.get("WP_URL", "https://www.culturaldaily.com").rstrip("/")
    auth = _wp_auth()
    if not auth[1]:
        print("Missing WP_PASS", file=sys.stderr)
        sys.exit(1)

    author_id = int(os.environ.get("OUR_FRIENDS_AUTHOR_ID", "19"))

    print(f"Fetching published posts (client-side author=={author_id})…", file=sys.stderr)
    posts = fetch_our_friends_posts(wp_url, author_id, auth)
    max_posts = os.environ.get("AUDIT_MAX_POSTS", "").strip()
    if max_posts.isdigit():
        posts = posts[: int(max_posts)]
        print(f"Capped to AUDIT_MAX_POSTS={max_posts}", file=sys.stderr)

    out: dict = {
        "wp_url": wp_url,
        "author_id": author_id,
        "post_count": len(posts),
        "posts": [],
        "note": (
            "WP REST returns HTTP 500 for ?author=19 on this site; published posts "
            "were scanned and filtered client-side."
        ),
    }

    media_cache: dict[int, dict] = {}
    hero_types: Counter = Counter()
    flag_counts: Counter = Counter()

    for idx, p in enumerate(posts):
        pid = p["id"]
        t = p.get("title") or {}
        title = t.get("raw") or t.get("rendered") or ""
        cobj = p.get("content") or {}
        content = cobj.get("raw") or cobj.get("rendered") or ""
        raw_html = content

        feat = p.get("featured_media") or 0
        hero: dict = {}
        if feat:
            if feat not in media_cache:
                media_cache[feat] = fetch_media(wp_url, feat, auth)
            media = media_cache[feat]
            if "error" not in media:
                md = media.get("media_details") or {}
                w = md.get("width") or 0
                h = md.get("height") or 0
                mt = media.get("title") or {}
                title_raw = mt.get("raw") or mt.get("rendered") or ""
                alt = media.get("alt_text") or ""
                mc = media.get("caption") or {}
                cap = mc.get("raw") or mc.get("rendered") or ""
                src = media.get("source_url") or ""
                host = urlparse(src).netloc if src else ""
                hero = {
                    "media_id": feat,
                    "width": w,
                    "height": h,
                    "hero_type": classify_hero(w, h),
                    "title": title_raw,
                    "alt_text_len": len(alt),
                    "caption_starts_photo": cap.strip().startswith("Photo:"),
                    "source_host": host,
                }
            else:
                hero = {"media_id": feat, "fetch_error": media}
        else:
            hero = {}

        content_flags = analyze_content(raw_html)

        flags: list[str] = []
        if not feat:
            flags.append("missing_featured_media")
        if hero.get("width") and hero.get("height"):
            if (hero["width"], hero["height"]) != CD_HERO:
                flags.append(f"hero_not_{CD_HERO[0]}x{CD_HERO[1]}")
        if content_flags["has_nextpage"]:
            flags.append("contains_nextpage")
        if content_flags["cite_uses_bold"]:
            flags.append("citation_bold")
        if content_flags["cite_any_photo"] and not content_flags["cite_pexels_em_a"]:
            flags.append("citation_not_em_a_pexels")
        if not content_flags["has_hr"]:
            flags.append("missing_hr")
        if not content_flags["has_donation_block"]:
            flags.append("missing_donation")
        if content_flags["order_photo_hr_donation"] is False:
            flags.append("bad_tail_order")
        if hero.get("title") and not re.match(
            r"^CD-[a-z0-9-]+-hero$", hero["title"], re.I
        ):
            flags.append("hero_title_not_cd_topic_hero")
        if hero.get("alt_text_len", 0) <= 10:
            flags.append("hero_alt_short_or_empty")

        if hero.get("hero_type"):
            hero_types[hero["hero_type"]] += 1
        for f in flags:
            flag_counts[f] += 1

        out["posts"].append(
            {
                "id": pid,
                "date": p.get("date"),
                "title": title[:120],
                "link": p.get("link"),
                "hero": hero,
                "content": content_flags,
                "flags": flags,
            }
        )
        if (idx + 1) % 400 == 0:
            print(f"  …processed {idx + 1} posts", file=sys.stderr)

    out["summary"] = {
        "posts_analyzed": len(out["posts"]),
        "unique_featured_media": len(media_cache),
        "hero_type_counts": dict(hero_types.most_common()),
        "flag_counts": dict(flag_counts.most_common()),
        "posts_with_any_flag": sum(1 for row in out["posts"] if row.get("flags")),
        "audit_max_posts_env": max_posts or None,
    }

    out_path = os.environ.get("AUDIT_JSON_OUT", "").strip()
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(json.dumps(out, indent=2))
        print(f"Wrote {out_path}", file=sys.stderr)

    json.dump(out, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
