#!/usr/bin/env python3
"""
Clear Yoast + AIOSEO OG on DCReport posts (cleanup accident / wrong pipeline OG).

Modes:
  Default — scan author 15 (Terry Schwadron) for any ``DCR-`` Yoast OG filename.
  ``--post-ids`` — clear OG on an explicit list of post IDs (see EDITORIAL_OG_CLEAR_POST_IDS).

Usage:
    python restore_dcr_editorial_og.py --dry-run
    python restore_dcr_editorial_og.py --post-ids --dry-run
    python restore_dcr_editorial_og.py --post-ids
"""
from __future__ import annotations

import argparse
import json
import re
import time
from typing import Iterator, List
from urllib.parse import unquote, urlparse

from pipeline import (
    REPO_ROOT,
    SITES,
    _aioseo_get_current,
    _apply_repo_dotenv_for_cli,
    _refresh_sites,
    _wp_post_meta_string,
    wp_auth,
)

SCHWADRON_AUTHOR_ID = 15
EXCLUDE_POST_IDS = frozenset({28926, 31477})  # legitimate editorial/pipeline OG — do not clear
SCAN_STATUSES = ("publish", "draft", "pending", "private", "future")

# 22 editorial posts with DCR-deferred-interest-financing-hero.jpg + 31200, 30532 (Tod Hardin publish)
EDITORIAL_OG_CLEAR_POST_IDS = frozenset({
    29560, 28723, 30354, 31040, 31215,
    25028, 25029, 25560, 28362,
    24908, 25628, 26183, 33055, 28647, 28655, 29579,
    28888, 29649, 30109, 30191, 31276, 32593,
    31200, 30532,
})
DCR_OG_BASENAME_RE = re.compile(r"DCR-[^/]+\.(?:jpe?g|png|webp)", re.I)


def _log(msg: str) -> None:
    print(msg, flush=True)


def _post_title(post: dict) -> str:
    t = post.get("title") or {}
    return (t.get("rendered") or t.get("raw") or "").strip()


def _yoast_og_from_post(post: dict) -> str:
    return _wp_post_meta_string(post, "_yoast_wpseo_opengraph-image")


def _og_url_basename(url: str) -> str:
    path = urlparse((url or "").strip()).path
    return unquote(path.rsplit("/", 1)[-1] if path else "")


def _yoast_og_has_dcr_filename(url: str) -> bool:
    """True when the OG URL points at a ``DCR-…`` media filename."""
    u = (url or "").strip()
    if not u:
        return False
    base = _og_url_basename(u)
    if base.upper().startswith("DCR-"):
        return True
    return bool(DCR_OG_BASENAME_RE.search(urlparse(u).path))


def _iter_schwadron_posts_with_dcr_yoast_og(wp: str, wp_sess) -> Iterator[dict]:
    """Batched wp/v2/posts; keep author 15 rows with DCR- in Yoast OG meta."""
    _log(f"[scan] author id={SCHWADRON_AUTHOR_ID} (Terry Schwadron), Yoast OG with DCR- filename…")
    matched = 0
    scanned = 0
    for status in SCAN_STATUSES:
        page = 1
        total_pages = 1
        while page <= total_pages:
            r = wp_sess.get(
                f"{wp}/wp-json/wp/v2/posts",
                params={
                    "status": status,
                    "per_page": 100,
                    "page": page,
                    "context": "edit",
                    "_fields": "id,title,status,date,author,meta",
                },
                timeout=90,
            )
            r.raise_for_status()
            batch = r.json()
            total_pages = int(r.headers.get("X-WP-TotalPages", "1") or 1)
            for post in batch:
                scanned += 1
                if int(post.get("author") or 0) != SCHWADRON_AUTHOR_ID:
                    continue
                yo = _yoast_og_from_post(post)
                if _yoast_og_has_dcr_filename(yo) and int(post["id"]) not in EXCLUDE_POST_IDS:
                    matched += 1
                    yield post
            page += 1
            time.sleep(0.05)
    _log(f"[scan] {matched} match(es) among {scanned} post rows checked")


def _find_candidate_posts(wp: str, wp_sess) -> List[dict]:
    return list(_iter_schwadron_posts_with_dcr_yoast_og(wp, wp_sess))


def _fetch_posts_by_ids(wp: str, wp_sess, post_ids: frozenset) -> List[dict]:
    want = sorted(pid for pid in post_ids if pid not in EXCLUDE_POST_IDS)
    _log(f"[scan] Fetching {len(want)} post(s) by ID…")
    out: List[dict] = []
    for pid in want:
        r = wp_sess.get(
            f"{wp}/wp-json/wp/v2/posts/{pid}",
            params={"context": "edit", "_fields": "id,title,status,date,author,meta"},
            timeout=60,
        )
        if r.status_code == 404:
            _log(f"  [warn] post {pid} not found")
            continue
        r.raise_for_status()
        out.append(r.json())
    return out


def _clear_yoast_og(wp: str, wp_sess, post_id: int) -> bool:
    pid = int(post_id)
    meta = {"_yoast_wpseo_opengraph-image": ""}
    r = wp_sess.post(f"{wp}/wp-json/wp/v2/posts/{pid}", json={"meta": meta}, timeout=60)
    if not r.ok:
        _log(f"  [warn] Yoast clear POST {r.status_code}: {r.text[:200]}")
        return False
    rv = wp_sess.get(f"{wp}/wp-json/wp/v2/posts/{pid}?context=edit", timeout=45)
    if rv.ok:
        left = _wp_post_meta_string(rv.json(), "_yoast_wpseo_opengraph-image")
        return not (left or "").strip()
    return r.ok


def _clear_aioseo_og(wp: str, wp_sess, post_id: int) -> bool:
    pid = int(post_id)
    current = _aioseo_get_current(wp, wp_sess, pid)
    if not current:
        return True

    cur_desc = (current.get("tags") or {}).get("description") or ""
    cur_kp_raw = current.get("keyphrases") or {}
    try:
        cur_kp = json.loads(cur_kp_raw) if isinstance(cur_kp_raw, str) else dict(cur_kp_raw or {})
    except Exception:
        cur_kp = {}

    body = {
        "id": pid,
        "default": False,
        "title": current.get("title") or "",
        "description": cur_desc,
        "og_image_type": "default",
        "og_image_custom_url": "",
        "og_image_custom": False,
        "og_title": current.get("og_title") or "",
        "og_description": current.get("og_description") or "",
        "twitter_title": current.get("twitter_title") or current.get("title") or "",
        "twitter_description": current.get("twitter_description") or cur_desc,
        "twitter_use_og": True,
        "twitter_image_custom_url": "",
        "keyphrases": json.dumps(cur_kp, ensure_ascii=False),
    }
    r = wp_sess.post(f"{wp}/wp-json/aioseo/v1/post", json=body, timeout=90)
    if r.ok:
        return True
    r2 = wp_sess.post(f"{wp}/wp-json/aioseo/v1/post/{pid}", json=body, timeout=90)
    return r2.ok


def _clear_og(site: dict, wp: str, wp_sess, post_id: int, *, dry_run: bool) -> List[str]:
    if dry_run:
        return ["would clear Yoast + AIOSEO OG to default/empty"]
    changes: List[str] = []
    if _clear_yoast_og(wp, wp_sess, post_id):
        changes.append("yoast og cleared")
    else:
        changes.append("yoast og clear uncertain")
    if _clear_aioseo_og(wp, wp_sess, post_id):
        changes.append("aioseo og cleared")
    else:
        changes.append("aioseo og clear failed or N/A")
    return changes


def run_restore(*, dry_run: bool = False, post_ids: bool = False) -> dict:
    _apply_repo_dotenv_for_cli()
    _refresh_sites()
    site = SITES["dcr"]
    wp = (site.get("wp_url") or "").rstrip("/")
    if not wp or not site.get("wp_user") or not site.get("wp_pass"):
        raise SystemExit("Missing DCR credentials in .env (WP_URL_DCR / WP_USER_DCR / WP_PASS_DCR)")

    mode = "post-ids" if post_ids else f"author-{SCHWADRON_AUTHOR_ID}"
    _log(f"[start] Clear Yoast/AIOSEO OG ({wp}) mode={mode} dry_run={dry_run}")
    if EXCLUDE_POST_IDS:
        _log(f"[start] Excluded post IDs: {sorted(EXCLUDE_POST_IDS)}")
    wp, wp_sess = wp_auth(site)

    if post_ids:
        candidates = sorted(
            _fetch_posts_by_ids(wp, wp_sess, EDITORIAL_OG_CLEAR_POST_IDS),
            key=lambda p: (p.get("date") or ""),
            reverse=True,
        )
    else:
        candidates = sorted(
            _find_candidate_posts(wp, wp_sess),
            key=lambda p: (p.get("date") or ""),
            reverse=True,
        )
    posts_fixed = 0
    log_rows: List[dict] = []

    _log(f"\n=== Full list ({len(candidates)} post(s)) ===\n")
    for i, post in enumerate(candidates, 1):
        post_id = int(post["id"])
        yoast_og = _yoast_og_from_post(post)
        title = _post_title(post)
        line = (
            f"{i:4}. post {post_id} ({post.get('status')}) "
            f"{str(post.get('date') or '')[:10]} | {title}"
        )
        _log(line)
        _log(f"      _yoast_wpseo_opengraph-image: {yoast_og}")
        log_rows.append(
            {
                "post_id": post_id,
                "status": post.get("status"),
                "date": post.get("date"),
                "title": title,
                "_yoast_wpseo_opengraph-image": yoast_og,
            }
        )

    if dry_run:
        _log(f"\n=== Dry-run: would clear OG on {len(candidates)} post(s) ===")
    else:
        _log(f"\n=== Clearing OG on {len(candidates)} post(s) ===")

    for post in candidates:
        post_id = int(post["id"])
        if post_id in EXCLUDE_POST_IDS:
            continue
        yoast_og = _yoast_og_from_post(post)
        if not dry_run:
            _log(f"\n[restore] post {post_id}…")
        changes = _clear_og(site, wp, wp_sess, post_id, dry_run=dry_run)
        if not dry_run:
            for c in changes:
                _log(f"  {c}")
        posts_fixed += 1
        time.sleep(0.12)

    summary = {
        "dry_run": dry_run,
        "mode": mode,
        "author_id": SCHWADRON_AUTHOR_ID if not post_ids else None,
        "post_ids": sorted(EDITORIAL_OG_CLEAR_POST_IDS) if post_ids else None,
        "wp_url": wp,
        "candidates_found": len(candidates),
        "posts_fixed": posts_fixed,
        "posts": log_rows,
    }
    _log(f"\n[done] matched={len(candidates)} fixed={posts_fixed} dry_run={dry_run}")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="List all matches; do not write")
    ap.add_argument(
        "--post-ids",
        action="store_true",
        help=f"Clear OG on EDITORIAL_OG_CLEAR_POST_IDS ({len(EDITORIAL_OG_CLEAR_POST_IDS)} posts)",
    )
    args = ap.parse_args()
    summary = run_restore(dry_run=args.dry_run, post_ids=args.post_ids)
    out = REPO_ROOT / "data" / "restore_dcr_editorial_og_log.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _log(f"[log] Wrote {out}")
    verb = "would be cleared" if summary["dry_run"] else "cleared"
    _log(f"\n=== FINAL: {summary['posts_fixed']} post(s) {verb} ===")


if __name__ == "__main__":
    main()
