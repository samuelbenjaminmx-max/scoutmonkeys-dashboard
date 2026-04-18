#!/usr/bin/env python3
"""
Audit **published** Cultural Daily posts by **Our Friends** (default author **19**) in a **time window**,
using the same HTML metrics as ``data/audit_format_profile.json``.

Use this to learn what sponsored **final WordPress HTML** looks like over the last year (or N days),
then align new drafts + ``format_to_audit_standard`` / QA with those norms.

Env: ``WP_URL``, ``WP_USER``, ``WP_PASS``, ``OUR_FRIENDS_AUTHOR_ID`` (default 19)

Examples::

  python3 scripts/audit_sponsored_last_year.py
  python3 scripts/audit_sponsored_last_year.py --days 365 --out-json data/sponsored_last_year_audit.json
  python3 scripts/audit_sponsored_last_year.py --after 2025-01-01T00:00:00 --out-md docs/CULTURAL_DAILY_SPONSORED_FORMAT_GUIDE.md
  python3 scripts/audit_sponsored_last_year.py --days 365 --write-category-allowlist

WordPress REST: paginates ``status=publish`` with ``after=`` ISO8601, filters ``author`` client-side
(same workaround as ``audit_our_friends_posts.py``).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wp_audit_aggregate import aggregate_rendered_posts  # noqa: E402


def cd_sponsor_category_forbidden_audit(slug: str, name: str) -> bool:
    """Match pipeline ``cd_sponsor_category_forbidden`` (Sponsored / Featured Story)."""
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


def fetch_category_rows(
    wp_url: str, auth: Tuple[str, str], ids: List[int]
) -> Dict[int, Tuple[str, str]]:
    out: Dict[int, Tuple[str, str]] = {}
    if not ids:
        return out
    chunk = 90
    for i in range(0, len(ids), chunk):
        batch = ids[i : i + chunk]
        r = requests.get(
            f"{wp_url}/wp-json/wp/v2/categories",
            auth=auth,
            params={"include": ",".join(str(x) for x in batch), "per_page": 100},
            timeout=90,
        )
        r.raise_for_status()
        for row in r.json():
            rid = int(row["id"])
            out[rid] = ((row.get("slug") or "").lower(), row.get("name") or "")
    return out


def aggregate_category_assignments(
    wp_url: str, auth: Tuple[str, str], posts: List[dict]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Count how often each category id appears on the filtered posts (posts can have multiple categories;
    each assignment increments the counter). Returns rows for JSON + sorted unique non-forbidden slugs
    for ``cd_sponsor_category_allowlist.json``.
    """
    ctr: Counter = Counter()
    for p in posts:
        for cid in p.get("categories") or []:
            try:
                ctr[int(cid)] += 1
            except (TypeError, ValueError):
                continue
    if not ctr:
        return [], []
    meta = fetch_category_rows(wp_url, auth, sorted(ctr.keys()))
    rows: List[Dict[str, Any]] = []
    for cid, cnt in ctr.most_common():
        slug, name = meta.get(cid, ("", ""))
        rows.append(
            {
                "id": cid,
                "slug": slug,
                "name": name,
                "post_assignments": int(cnt),
                "cd_sponsor_forbidden": cd_sponsor_category_forbidden_audit(slug, name),
            }
        )
    allow = sorted({r["slug"] for r in rows if r["slug"] and not r["cd_sponsor_forbidden"]})
    return rows, allow


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


def fetch_posts_author_since(
    wp_url: str,
    author_id: int,
    auth: Tuple[str, str],
    *,
    after_iso: str,
) -> List[dict]:
    posts: List[dict] = []
    page = 1
    fields = "id,date,date_gmt,link,author,content,categories"
    while True:
        r = requests.get(
            f"{wp_url}/wp-json/wp/v2/posts",
            auth=auth,
            params={
                "status": "publish",
                "per_page": 100,
                "page": page,
                "after": after_iso,
                "_fields": fields,
            },
            timeout=120,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        for p in batch:
            if int(p.get("author") or 0) == author_id:
                posts.append(p)
        total_pages = int(r.headers.get("X-WP-TotalPages", "1"))
        if page >= total_pages:
            break
        page += 1
        if page % 10 == 0:
            print(f"  …page {page}/{total_pages}", file=sys.stderr)
    return posts


def render_format_guide_md(data: Dict[str, Any]) -> str:
    """Human-oriented rules doc (like training a new editor)."""
    meta = data.get("meta") or {}
    agg = data.get("aggregate") or {}
    th = data.get("thresholds") or {}
    top = data.get("top_20_patterns") or []
    tags = data.get("top_tag_counts") or []

    lines = [
        "# Cultural Daily — Sponsored (Our Friends) format guide",
        "",
        "_This file is generated by `scripts/audit_sponsored_last_year.py`. Regenerate after refreshing audits._",
        "",
        "## Purpose",
        "",
        "Train operators and automation on **what we actually publish**: sponsored posts by **Our Friends** on Cultural Daily. ",
        "This is **not** a license to invent layout—**`CLAUDE.md`**, **`CRITICAL_RULES.md`**, and **`QA.md`** still win when they conflict. ",
        "Use this guide to spot **drift** (a new Google Doc or draft that looks nothing like recent live posts).",
        "",
        "## Data sources (pipeline mental model)",
        "",
        "| Stage | What we measure | Where |",
        "|--------|-----------------|--------|",
        "| **Client input** | Google Doc HTML export | URLs in `data/training_docs.txt`; batch parse `python3 doc_parser.py --batch …` |",
        "| **This audit** | Final `content.rendered` on **published** posts | REST `after=` window, author filtered client-side |",
        "| **Full historical profile** | Same metrics, all-time published Our Friends | `scripts/build_audit_format_profile.py` → `data/audit_format_profile.json` |",
        "",
        "## Current audit window",
        "",
        f"- **After (UTC):** `{meta.get('after_iso', '?')}`",
        f"- **Rolling days (if used):** {meta.get('rolling_days', 'n/a')}",
        f"- **Posts matched:** {meta.get('posts_matched', '?')}",
        f"- **Posts with rendered HTML:** {meta.get('posts_with_rendered_html', '?')}",
        f"- **WP URL:** `{meta.get('wp_url', '?')}`",
        f"- **Author ID (Our Friends):** {meta.get('author_id', '?')}",
        "",
    ]
    cats = data.get("category_slug_counts") or []
    if cats:
        lines.extend(
            [
                "## WordPress categories (Our Friends posts in this window)",
                "",
                "Each row is one **category id**; `post_assignments` counts posts that include that category. ",
                "Use this to see real mixes (e.g. Check This Out vs Grey Niche). Automation must **never** use a row marked `cd_sponsor_forbidden`. ",
                "Regenerate `data/cd_sponsor_category_allowlist.json` with `--write-category-allowlist` after refreshing this audit.",
                "",
                "| slug | name | assignments | forbidden for CD sponsor |",
                "|------|------|-------------:|----------------------------|",
            ]
        )
        for row in cats[:40]:
            lines.append(
                f"| `{row.get('slug', '')}` | {row.get('name', '')} | {row.get('post_assignments', 0)} | "
                f"{'yes' if row.get('cd_sponsor_forbidden') else 'no'} |"
            )
        lines.append("")

    lines.extend(
        [
            "## Empirical aggregates (this window)",
            "",
            "| Metric | Value |",
            "|--------|-------|",
        ]
    )
    for k, v in agg.items():
        lines.append(f"| `{k}` | {v} |")
    lines.extend(
        [
            "",
            "## Thresholds suggested for `format_to_audit_standard` / profile JSON",
            "",
            "These mirror `audit_format_profile.json` logic; refresh both periodically.",
            "",
            "| Key | Value |",
            "|-----|-------|",
        ]
    )
    for k, v in th.items():
        lines.append(f"| `{k}` | {v} |")

    lines.extend(
        [
            "",
            "## Top tag counts (published body HTML)",
            "",
            "| Tag | Count (aggregate) |",
            "|-----|------------------:|",
        ]
    )
    for row in tags[:18]:
        lines.append(f"| `<{row.get('tag', '?')}>` | {row.get('count', 0)} |")

    lines.extend(
        [
            "",
            "## Top formatting patterns (ranked)",
            "",
        ]
    )
    for row in top[:20]:
        lines.append(f"{row.get('rank', '?')}. **{row.get('pattern_key', '')}** — {row.get('description', '')}")

    lines.extend(
        [
            "",
            "## Rules for new WordPress drafts (human checklist)",
            "",
            "1. **Body HTML** comes from the **Google Doc export** + pipeline transforms—not from freeform pasting in WP.",
            "2. **Sponsored body links** must match the hard shape in `QA.md` (bold + `target=_blank`, dofollow).",
            "3. **Spacing:** corpus favors **single-line** gaps between tags; pipeline collapses runaway newlines.",
            "4. **H2:** if most live posts avoid leading `1.` / `2)` markers, expect `format_to_audit_standard` to strip ordinals when the profile says so.",
            "5. **Images:** hero is featured image; in-article images follow `CD-InsertN` naming + centered figure markup after `remediate-latest` / publish path.",
            "6. **When in doubt:** run `python3 pipeline.py remediate-latest cd` on the draft and fix QA failures before calling the job done.",
            "",
            "## Exceptions",
            "",
        ]
    )
    nf = float(agg.get("numbered_h2_fraction") or 0)
    lines.append(
        f"- **Numbered H2s** appear on a minority of posts (~**{nf:.1%}** of H2s in this window). "
        "The pipeline may still normalize ordinals toward the dominant style—verify with stakeholders if a client insists on numbers."
    )
    lines.extend(
        [
            "",
            "## Refresh",
            "",
            "```bash",
            "python3 scripts/audit_sponsored_last_year.py --days 365 \\",
            "  --out-json data/sponsored_last_year_audit.json \\",
            f"  --out-md {ROOT}/docs/CULTURAL_DAILY_SPONSORED_FORMAT_GUIDE.md \\",
            "  --write-category-allowlist",
            "```",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365, help="Rolling window from now (UTC); ignored if --after set")
    ap.add_argument(
        "--after",
        default="",
        help="ISO8601 lower bound for post date, e.g. 2025-01-01T00:00:00 (overrides --days)",
    )
    ap.add_argument("--out-json", default=str(ROOT / "data" / "sponsored_last_year_audit.json"))
    ap.add_argument("--out-md", default=str(ROOT / "docs" / "CULTURAL_DAILY_SPONSORED_FORMAT_GUIDE.md"))
    ap.add_argument(
        "--write-category-allowlist",
        action="store_true",
        help="Write data/cd_sponsor_category_allowlist.json from non-forbidden slugs in this window",
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

    if (args.after or "").strip():
        after_iso = args.after.strip()
        rolling = None
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(args.days)))
        after_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%S")
        rolling = int(args.days)

    print(f"Fetching posts published after {after_iso} (author={author_id})…", file=sys.stderr)
    posts = fetch_posts_author_since(wp_url, author_id, auth, after_iso=after_iso)
    agg, posts_with_html = aggregate_rendered_posts(posts)
    cat_rows, allow_slugs = aggregate_category_assignments(wp_url, auth, posts)

    out: Dict[str, Any] = {
        "meta": {
            "source": "WordPress content.rendered for Our Friends published posts in time window",
            "wp_url": wp_url,
            "author_id": author_id,
            "after_iso": after_iso,
            "rolling_days": rolling,
            "posts_matched": len(posts),
            "posts_with_rendered_html": posts_with_html,
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        **agg,
        "category_slug_counts": cat_rows,
    }

    if args.write_category_allowlist:
        allow_path = ROOT / "data" / "cd_sponsor_category_allowlist.json"
        allow_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "_comment": (
                "Non-forbidden category slugs observed in the sponsored audit window; used by pipeline "
                "``resolve_cd_sponsored_category`` so planner hints (e.g. grey-niche) map only to audited lanes. "
                "Regenerate with: python3 scripts/audit_sponsored_last_year.py --write-category-allowlist"
            ),
            "slugs": allow_slugs,
        }
        allow_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {allow_path} ({len(allow_slugs)} slugs)", file=sys.stderr)

    outp = Path(args.out_json)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {outp}", file=sys.stderr)

    md_path = Path(args.out_md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_format_guide_md(out), encoding="utf-8")
    print(f"Wrote {md_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
