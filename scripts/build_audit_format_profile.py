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
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests
from bs4 import BeautifulSoup, Comment

ROOT = Path(__file__).resolve().parent.parent


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


def _h2_structure_signature(h2) -> str:
    """Tag skeleton for H2 (text replaced with *)."""
    parts: List[str] = ["h2"]
    for child in h2.children:
        if isinstance(child, Comment):
            continue
        if getattr(child, "name", None):
            parts.append(child.name or "?")
        elif str(child).strip():
            parts.append("#text")
    return ">".join(parts)


def analyze_html(html: str) -> Dict[str, Any]:
    """Per-post metrics (no storage of full HTML)."""
    if not (html or "").strip():
        return {}
    raw = html
    soup = BeautifulSoup(html, "html.parser")
    h2_total = 0
    h2_numbered = 0
    h2_sigs: Counter = Counter()
    for h2 in soup.find_all("h2"):
        h2_total += 1
        t = h2.get_text(" ", strip=True)
        if t and re.match(r"^\s*\d+\s*[\.\)\-:]\s+\S", t):
            h2_numbered += 1
        h2_sigs[_h2_structure_signature(h2)] += 1

    tag_names: Counter = Counter()
    for el in soup.find_all(True):
        tag_names[el.name.lower()] += 1

    ul_n = len(soup.find_all("ul"))
    ol_n = len(soup.find_all("ol"))
    li_n = len(soup.find_all("li"))

    # Adjacent block-tag gaps in serialized HTML (WP often stores compactly).
    gap_single = len(re.findall(r">\s*\n\s*<", raw))
    gap_double = len(re.findall(r">\s*\n\s*\n\s*<", raw))
    gap_triple = len(re.findall(r">\s*\n\s*\n\s*\n\s*<", raw))

    a_in_p = sum(1 for a in soup.find_all("a") if a.find_parent("p") is not None)
    strong_in_p = sum(1 for s in soup.find_all("strong") if s.find_parent("p") is not None)

    return {
        "h2_total": h2_total,
        "h2_numbered": h2_numbered,
        "h2_sigs": h2_sigs,
        "tag_names": tag_names,
        "ul": ul_n,
        "ol": ol_n,
        "li": li_n,
        "gap_single": gap_single,
        "gap_double": gap_double,
        "gap_triple": gap_triple,
        "anchors_in_p": a_in_p,
        "strong_in_p": strong_in_p,
    }


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

    total_tag: Counter = Counter()
    total_h2_sig: Counter = Counter()
    sum_h2 = 0
    sum_h2_num = 0
    sum_ul = sum_ol = sum_li = 0
    sum_gap_s = sum_gap_d = sum_gap_t = 0
    sum_a_p = sum_str_p = 0
    posts_with_html = 0

    for p in posts:
        html = (p.get("content") or {}).get("rendered") or ""
        if not html.strip():
            continue
        posts_with_html += 1
        m = analyze_html(html)
        if not m:
            continue
        sum_h2 += m["h2_total"]
        sum_h2_num += m["h2_numbered"]
        total_h2_sig.update(m["h2_sigs"])
        total_tag.update(m["tag_names"])
        sum_ul += m["ul"]
        sum_ol += m["ol"]
        sum_li += m["li"]
        sum_gap_s += m["gap_single"]
        sum_gap_d += m["gap_double"]
        sum_gap_t += m["gap_triple"]
        sum_a_p += m["anchors_in_p"]
        sum_str_p += m["strong_in_p"]

    # Top 20 "patterns" as human-readable + machine keys
    top_tags = total_tag.most_common(30)
    top_h2_struct = total_h2_sig.most_common(10)

    numbered_fraction = (sum_h2_num / sum_h2) if sum_h2 else 0.0
    list_total = sum_ul + sum_ol
    ul_fraction = (sum_ul / list_total) if list_total else 1.0

    patterns: List[Dict[str, Any]] = []
    rank = 0
    for tag, cnt in top_tags[:15]:
        rank += 1
        patterns.append(
            {
                "rank": rank,
                "pattern_key": f"tag:{tag}",
                "count": int(cnt),
                "description": f"Element <{tag}> appears {cnt} times across analyzed posts (aggregate).",
            }
        )
    for sig, cnt in top_h2_struct[:5]:
        rank += 1
        patterns.append(
            {
                "rank": rank,
                "pattern_key": f"h2_structure:{sig}",
                "count": int(cnt),
                "description": f"H2 child structure “{sig}” occurs {cnt} times (skeleton, text ignored).",
            }
        )
    patterns.append(
        {
            "rank": rank + 1,
            "pattern_key": "spacing:inter_tag_single_newline",
            "count": int(sum_gap_s),
            "description": f"Serialized `>\\n<` gaps (aggregate): {sum_gap_s}",
        }
    )
    patterns.append(
        {
            "rank": rank + 2,
            "pattern_key": "spacing:inter_tag_double_newline",
            "count": int(sum_gap_d),
            "description": f"Serialized `>\\n\\n<` gaps (aggregate): {sum_gap_d}",
        }
    )
    patterns.append(
        {
            "rank": rank + 3,
            "pattern_key": "links:anchor_inside_p",
            "count": int(sum_a_p),
            "description": f"Anchors nested under <p> (aggregate): {sum_a_p}",
        }
    )
    patterns.append(
        {
            "rank": rank + 4,
            "pattern_key": "inline:strong_inside_p",
            "count": int(sum_str_p),
            "description": f"<strong> inside <p> (aggregate): {sum_str_p}",
        }
    )
    # Trim to top 20 by count
    patterns.sort(key=lambda x: -x["count"])
    top_20 = []
    for i, row in enumerate(patterns[:20], start=1):
        row = dict(row)
        row["rank"] = i
        top_20.append(row)

    thresholds = {
        "strip_h2_leading_ordinals": bool(numbered_fraction < 0.5),
        "numbered_h2_fraction_observed": round(numbered_fraction, 5),
        "collapse_runs_of_newlines_ge_3": True,
        "max_serialized_newline_run": 2,
        "lists_ul_share_observed": round(ul_fraction, 5),
    }

    out: Dict[str, Any] = {
        "source": "WordPress content.rendered for author=19 published posts (same cohort as our_friends_audit.json)",
        "wp_url": wp_url,
        "author_id": author_id,
        "posts_fetched": len(posts),
        "posts_with_rendered_html": posts_with_html,
        "aggregate": {
            "h2_total": sum_h2,
            "h2_numbered_total": sum_h2_num,
            "numbered_h2_fraction": round(numbered_fraction, 5),
            "ul_total": sum_ul,
            "ol_total": sum_ol,
            "li_total": sum_li,
            "inter_tag_gap_single": sum_gap_s,
            "inter_tag_gap_double": sum_gap_d,
            "inter_tag_gap_triple_plus": sum_gap_t,
        },
        "top_tag_counts": [{"tag": t, "count": int(c)} for t, c in top_tags[:25]],
        "top_h2_structure_signatures": [{"signature": s, "count": int(c)} for s, c in top_h2_struct],
        "top_20_patterns": top_20,
        "thresholds": thresholds,
    }

    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {outp}", file=sys.stderr)


if __name__ == "__main__":
    main()
