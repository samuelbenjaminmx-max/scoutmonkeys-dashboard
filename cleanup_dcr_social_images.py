#!/usr/bin/env python3
"""
Remove duplicate DCR ``-social`` media attachments and point OG images at the hero.

Only processes posts created by the pipeline: body contains ``scoutmonkeys-gdoc:`` or
``scoutmonkeys-machine-tail``, author matches ``DCR_AUTHOR_ID``, and ``featured_media`` is a
``DCR-{topic}-hero`` attachment with a matching ``DCR-{topic}-social`` sibling.

1. Index ``DCR-*-hero`` and ``DCR-*-social`` rows in the media library (``search=DCR-``).
2. Scan publish/draft posts for pipeline markers (``scoutmonkeys-gdoc`` / machine tail).
3. When a pipeline post's featured image is ``DCR-{topic}-hero`` and a separate
   ``-social`` exists, set Yoast + AIOSEO OG to the hero URL and delete the social row.

Usage:
    python cleanup_dcr_social_images.py           # live run
    python cleanup_dcr_social_images.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import time
from typing import Dict, Iterator, List, Optional, Tuple

from pipeline import (
    REPO_ROOT,
    SITES,
    _aioseo_get_current,
    _apply_repo_dotenv_for_cli,
    _refresh_sites,
    cd_delete_wp_media_attachment,
    push_aioseo_and_cdseo,
    wp_auth,
)

POST_STATUSES = ("publish", "draft", "pending", "private", "future")


def _log(msg: str) -> None:
    print(msg, flush=True)


def _media_title(item: dict) -> str:
    t = item.get("title") or {}
    return (t.get("raw") or t.get("rendered") or "").strip()


def _topic_slug_from_attachment_title(title: str, prefix: str) -> Optional[str]:
    t = title.strip()
    for suffix in ("-hero", "-social"):
        m = re.match(rf"^{re.escape(prefix)}-(.+){re.escape(suffix)}$", t, re.I)
        if m:
            return m.group(1).lower()
    # Legacy pipeline hero: ``DCR-{topic}`` without ``-hero`` suffix
    m = re.match(rf"^{re.escape(prefix)}-([a-z0-9-]+)$", t, re.I)
    if m:
        slug = m.group(1).lower()
        if slug.endswith("-social") or re.search(r"-insert\d*$", slug, re.I):
            return None
        return slug
    return None


def _is_pipeline_hero_media(item: dict, prefix: str) -> bool:
    """Pipeline titles featured images as ``{prefix}-{topic}-hero`` (or legacy ``-topic``)."""
    return _topic_slug_from_attachment_title(_media_title(item), prefix) is not None and not _is_dcr_social_media(
        item, prefix
    )


def _is_dcr_social_media(item: dict, prefix: str) -> bool:
    title = _media_title(item)
    if title.lower().endswith("-social") and title.upper().startswith(f"{prefix}-"):
        return True
    url = (item.get("source_url") or "").strip()
    if re.search(rf"{re.escape(prefix)}-.+-social\.(jpe?g|png|webp)", url, re.I):
        return True
    slug = (item.get("slug") or "").strip()
    return bool(slug and "-social" in slug.lower() and prefix.lower() in slug.lower())


def _iter_media_pages(wp: str, wp_sess, *, search: str) -> Iterator[dict]:
    page = 1
    total_pages = 1
    while page <= total_pages:
        _log(f"[scan] media search={search!r} page {page}/{total_pages}…")
        r = wp_sess.get(
            f"{wp}/wp-json/wp/v2/media",
            params={
                "search": search,
                "per_page": 100,
                "page": page,
                "orderby": "date",
                "order": "desc",
            },
            timeout=90,
        )
        r.raise_for_status()
        batch = r.json()
        total_pages = int(r.headers.get("X-WP-TotalPages", "1") or 1)
        if not batch:
            break
        yield from batch
        page += 1
        time.sleep(0.08)


def _build_pipeline_media_indexes(
    site: dict,
) -> Tuple[Dict[str, Tuple[int, dict]], Dict[str, int], Dict[int, dict]]:
    """One ``DCR-`` media pass → hero index + social index."""
    wp, wp_sess = wp_auth(site)
    prefix = site["prefix"]
    hero_by_slug: Dict[str, Tuple[int, dict]] = {}
    social_by_slug: Dict[str, int] = {}
    social_by_id: Dict[int, dict] = {}
    hero_seen = social_seen = 0
    for item in _iter_media_pages(wp, wp_sess, search="DCR-"):
        title = _media_title(item)
        slug = _topic_slug_from_attachment_title(title, prefix)
        if _is_pipeline_hero_media(item, prefix) and slug and slug not in hero_by_slug:
            hero_seen += 1
            hero_by_slug[slug] = (int(item["id"]), item)
        elif _is_dcr_social_media(item, prefix) and slug and slug not in social_by_slug:
            social_seen += 1
            mid = int(item["id"])
            social_by_slug[slug] = mid
            social_by_id[mid] = item
    _log(
        f"[scan] {len(hero_by_slug)} pipeline hero(s) ({hero_seen} rows), "
        f"{len(social_by_slug)} -social slug(s) ({social_seen} rows) in DCR- media"
    )
    return hero_by_slug, social_by_slug, social_by_id


def _get_media(wp: str, wp_sess, media_id: int, cache: Dict[int, dict]) -> dict:
    mid = int(media_id)
    if mid in cache:
        return cache[mid]
    r = wp_sess.get(f"{wp}/wp-json/wp/v2/media/{mid}?context=edit", timeout=60)
    r.raise_for_status()
    row = r.json()
    cache[mid] = row
    return row


def _is_pipeline_post(post: dict) -> bool:
    """Pipeline drafts include a gdoc marker and/or machine tail."""
    c = post.get("content") or {}
    raw = (c.get("raw") or c.get("rendered") or "")
    return "scoutmonkeys-gdoc:" in raw or "scoutmonkeys-machine-tail" in raw


def _iter_pipeline_posts(wp: str, wp_sess) -> Iterator[dict]:
    """Yield publish/draft posts created by the Scoutmonkeys pipeline."""
    for status in ("publish", "draft"):
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
                    "_fields": "id,title,slug,status,featured_media,link,content",
                },
                timeout=90,
            )
            r.raise_for_status()
            batch = r.json()
            total_pages = int(r.headers.get("X-WP-TotalPages", "1") or 1)
            for post in batch:
                if _is_pipeline_post(post):
                    yield post
            page += 1
            time.sleep(0.08)


def _update_og_to_hero(site: dict, post_id: int, hero_url: str, *, dry_run: bool) -> List[str]:
    hero_url = (hero_url or "").strip()
    if not hero_url:
        return ["skip: empty hero URL"]
    if dry_run:
        return [f"would set OG → {hero_url} (Yoast + AIOSEO)"]

    wp, wp_sess = wp_auth(site)
    push_aioseo_and_cdseo(site, post_id, {}, hero_url)
    changes = [f"yoast og → {hero_url}"]

    pid = int(post_id)
    current = _aioseo_get_current(wp, wp_sess, pid)
    if not current:
        return changes

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
        "og_image_type": "custom_image",
        "og_image_custom_url": hero_url,
        "og_image_custom": True,
        "og_title": current.get("og_title") or "",
        "og_description": current.get("og_description") or "",
        "twitter_title": current.get("twitter_title") or current.get("title") or "",
        "twitter_description": current.get("twitter_description") or cur_desc,
        "twitter_use_og": True,
        "twitter_image_custom_url": hero_url,
        "keyphrases": json.dumps(cur_kp, ensure_ascii=False),
    }
    r = wp_sess.post(f"{wp}/wp-json/aioseo/v1/post", json=body, timeout=90)
    if r.ok:
        changes.append(f"aioseo og → {hero_url}")
    else:
        r2 = wp_sess.post(f"{wp}/wp-json/aioseo/v1/post/{pid}", json=body, timeout=90)
        if r2.ok:
            changes.append(f"aioseo og → {hero_url} (path fallback)")
        else:
            changes.append(f"aioseo POST failed ({r.status_code}); yoast updated only")
    return changes


def _post_title(post: dict) -> str:
    t = post.get("title") or {}
    return (t.get("rendered") or t.get("raw") or "").strip()


def run_cleanup(*, dry_run: bool = False, limit: Optional[int] = None) -> dict:
    _apply_repo_dotenv_for_cli()
    _refresh_sites()
    site = SITES["dcr"]
    wp = (site.get("wp_url") or "").rstrip("/")
    if not wp or not site.get("wp_user") or not site.get("wp_pass"):
        raise SystemExit(
            "Missing DCR credentials (WP_URL_DCR / DCR_WP_URL, WP_USER_DCR, WP_PASS_DCR in .env)"
        )

    _log(f"[start] DCR social cleanup (pipeline heroes only) on {wp} dry_run={dry_run}")
    wp, wp_sess = wp_auth(site)

    prefix = site["prefix"]
    hero_by_slug, social_by_slug, social_by_id = _build_pipeline_media_indexes(site)
    media_cache: Dict[int, dict] = dict(social_by_id)

    _log("[scan] Listing pipeline posts (publish + draft)…")
    posts_cleaned = 0
    social_deleted = 0
    pipeline_posts_seen = 0
    log_lines: List[str] = []
    social_removed_slugs: set = set()

    for post in _iter_pipeline_posts(wp, wp_sess):
        if limit is not None and pipeline_posts_seen >= limit:
            break
        pipeline_posts_seen += 1

        post_id = int(post["id"])
        hero_id = int(post.get("featured_media") or 0)
        if not hero_id:
            continue

        try:
            hero = _get_media(wp, wp_sess, hero_id, media_cache)
        except Exception as exc:
            log_lines.append(f"post {post_id}: skip — hero {hero_id}: {exc}")
            continue

        hero_title = _media_title(hero)
        if not _is_pipeline_hero_media(hero, prefix):
            continue

        topic_slug = _topic_slug_from_attachment_title(hero_title, prefix)
        if not topic_slug:
            continue

        social_id = social_by_slug.get(topic_slug)
        if not social_id or int(social_id) == hero_id:
            continue

        hero_url = (hero.get("source_url") or "").strip()
        social = social_by_id.get(int(social_id), {})
        social_title = _media_title(social) if social else f"id={social_id}"

        header = (
            f"post {post_id} ({post.get('status')}) {_post_title(post)[:55]!r} | "
            f"hero={hero_id} ({hero_title}) | social={social_id} ({social_title})"
        )
        _log(f"\n[cleanup] {header}")

        og_changes = _update_og_to_hero(site, post_id, hero_url, dry_run=dry_run)
        for line in og_changes:
            _log(f"  OG: {line}")

        if topic_slug not in social_removed_slugs:
            if dry_run:
                del_msg = f"would DELETE media id={social_id}"
            else:
                cd_delete_wp_media_attachment(site, int(social_id))
                del_msg = f"deleted media id={social_id}"
                social_deleted += 1
                social_by_slug.pop(topic_slug, None)
                social_by_id.pop(int(social_id), None)
                social_removed_slugs.add(topic_slug)
            _log(f"  {del_msg}")
        else:
            del_msg = f"social id={social_id} already removed for slug {topic_slug!r}"

        posts_cleaned += 1
        log_lines.append(f"{header} | {'; '.join(og_changes)} | {del_msg}")
        time.sleep(0.2)

    orphan_social = len(social_by_id)
    summary = {
        "dry_run": dry_run,
        "wp_url": wp,
        "pipeline_heroes_in_media": len(hero_by_slug),
        "pipeline_posts_seen": pipeline_posts_seen,
        "posts_cleaned": posts_cleaned,
        "social_deleted": social_deleted,
        "orphan_social_remaining": orphan_social,
        "log": log_lines,
    }
    _log(
        f"\n[done] pipeline posts seen={pipeline_posts_seen} "
        f"posts_cleaned={posts_cleaned} social_deleted={social_deleted} "
        f"orphan_social={orphan_social} dry_run={dry_run}"
    )
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Log only; no OG updates or deletes")
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after N pipeline hero slugs (debug)",
    )
    args = ap.parse_args()
    summary = run_cleanup(dry_run=args.dry_run, limit=args.limit)
    out_path = REPO_ROOT / "data" / "cleanup_dcr_social_images_log.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _log(f"[log] Wrote {out_path}")
    _log(f"\n=== FINAL: {summary['posts_cleaned']} post(s) cleaned ===")


if __name__ == "__main__":
    main()
