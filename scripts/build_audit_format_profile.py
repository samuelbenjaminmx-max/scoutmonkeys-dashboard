#!/usr/bin/env python3
"""
Build ``data/audit_format_profile.json`` from the same Our Friends corpus as
``our_friends_audit.json``: fetch published posts (author 19), analyze
``content.rendered`` HTML, and emit top formatting patterns + thresholds for
``pipeline.format_to_audit_standard``.

Env: WP_URL, WP_USER, WP_PASS, OUR_FRIENDS_AUTHOR_ID (default 19), AUDIT_MAX_POSTS

Usage:
  python3 scripts/build_audit_format_profile.py
  python3 scripts/build_audit_format_profile.py --out data/audit_format_profile.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wp_audit_aggregate import aggregate_rendered_posts  # noqa: E402


def load_env() -> None:
    for p in (ROOT / ".env", Path.cwd() / ".env"):
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, _, v = s.partition("=")
            k, v = k.strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            os.environ[k] = v
        return


def fetch_posts(wp_url: str, author_id: int, auth: Tuple[str, str]) -> List[dict]:
    posts: List[dict] = []
    page = 1
    fields = "id,author,content"
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
            timeout=120,
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
            print(f"  …list page {page}/{total_pages}", file=sys.stderr)
    return posts


def main() -> None:
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out",
        default=str(ROOT / "data" / "audit_format_profile.json"),
        help="Output JSON path",
    )
    args = ap.parse_args()

    wp_url = os.environ.get("WP_URL", "https://www.culturaldaily.com").rstrip("/")
    user = os.environ.get("WP_USER", "")
    pw = os.environ.get("WP_PASS", "")
    if not pw:
        print("Missing WP_PASS (set in .env)", file=sys.stderr)
        sys.exit(1)
    auth = (user, pw)
    author_id = int(os.environ.get("OUR_FRIENDS_AUTHOR_ID", "19"))

    print("Fetching published posts + rendered HTML…", file=sys.stderr)
    posts = fetch_posts(wp_url, author_id, auth)
    cap = os.environ.get("AUDIT_MAX_POSTS", "").strip()
    if cap.isdigit():
        posts = posts[: int(cap)]
        print(f"Capped to AUDIT_MAX_POSTS={cap}", file=sys.stderr)

    agg, posts_with_html = aggregate_rendered_posts(posts)
    out: Dict[str, Any] = {
        "source": "WordPress content.rendered for author=19 published posts (same cohort as our_friends_audit.json)",
        "wp_url": wp_url,
        "author_id": author_id,
        "posts_fetched": len(posts),
        "posts_with_rendered_html": posts_with_html,
        **agg,
    }

    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {outp}", file=sys.stderr)


if __name__ == "__main__":
    main()
