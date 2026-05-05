#!/usr/bin/env python3
"""
learn_from_edits.py
-------------------
Diffs a pipeline draft snapshot against the live published WP post,
detects manual edits, classifies them as rule deltas, and appends to
data/learned_rules.json.

Usage:
    python3 learn_from_edits.py <post_id> <site>
    python3 learn_from_edits.py 33347 dcr

Requires:
    - data/drafts/<post_id>.json  (saved by pipeline at draft creation)
    - WP creds in env/.env (see SITES mapping below)
    - pip install python-dotenv requests beautifulsoup4
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests
from bs4 import BeautifulSoup

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

SITES = {
    "cd": {
        "base_url": "https://www.culturaldaily.com",
        "wp_user_envs": ("CD_WP_USER", "WP_USER"),
        "wp_pass_envs": ("CD_WP_APP_PASSWORD", "WP_PASS"),
    },
    "dcr": {
        "base_url": "https://www.dcreport.org",
        "wp_user_envs": ("WP_USER_DCR",),
        "wp_pass_envs": ("WP_PASS_DCR",),
    },
}

DRAFTS_DIR = Path("data/drafts")
LEARNED_RULES_FILE = Path("data/learned_rules.json")


def _first_env(*keys: str) -> str:
    for k in keys:
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return ""


def save_draft_snapshot(post_id: int, payload: Dict[str, Any]) -> Path:
    """Persist draft-time snapshot for later edit learning."""
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    out = DRAFTS_DIR / f"{int(post_id)}.json"
    snap = {
        "post_id": int(post_id),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        **(payload or {}),
    }
    out.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def fetch_published_post(post_id: int, site: str) -> Dict[str, Any]:
    cfg = SITES[site]
    user = os.environ.get("WP_USER_DCR", "")
    password = os.environ.get("WP_PASS_DCR", "")
    url = f"{cfg['base_url']}/wp-json/wp/v2/posts/{int(post_id)}"
    resp = requests.get(
        url, auth=(user, password), headers={"User-Agent": "Mozilla/5.0"}, timeout=20
    )
    resp.raise_for_status()
    return resp.json()


def load_draft_snapshot(post_id: int) -> Dict[str, Any]:
    path = DRAFTS_DIR / f"{int(post_id)}.json"
    if not path.exists():
        raise FileNotFoundError(f"No draft snapshot found at {path}.")
    return json.loads(path.read_text(encoding="utf-8"))


def _strip_machine_tail(html: str) -> str:
    h = html or ""
    return h.split("<!--scoutmonkeys-machine-tail-->")[0].strip()


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def extract_links(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html or "", "html.parser")
    links: List[Dict[str, Any]] = []
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        rel = a.get("rel") or []
        if isinstance(rel, str):
            rel = rel.split()
        links.append(
            {
                "href": href,
                "text": a.get_text(" ", strip=True)[:120],
                "bold": bool(a.find("strong") or a.find_parent("strong")),
                "nofollow": "nofollow" in [str(x).lower() for x in rel],
                "new_tab": (a.get("target") or "") == "_blank",
            }
        )
    return links


def extract_images(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html or "", "html.parser")
    imgs: List[Dict[str, Any]] = []
    for i, img in enumerate(soup.find_all("img"), start=1):
        imgs.append(
            {
                "slot": i,
                "src": (img.get("src") or "").strip(),
                "alt": (img.get("alt") or "").strip(),
                "title": (img.get("title") or "").strip(),
            }
        )
    return imgs


def classify_rule_deltas(draft: Dict[str, Any], live_post: Dict[str, Any], site: str) -> List[Dict[str, Any]]:
    deltas: List[Dict[str, Any]] = []
    draft_title = (draft.get("title") or "").strip()
    draft_body = _strip_machine_tail(draft.get("body_html") or "")
    live_title = ((live_post.get("title") or {}).get("raw") or (live_post.get("title") or {}).get("rendered") or "").strip()
    live_body = _strip_machine_tail((live_post.get("content") or {}).get("raw") or (live_post.get("content") or {}).get("rendered") or "")

    if _norm_text(draft_title) != _norm_text(live_title):
        deltas.append(
            {
                "type": "title_edit",
                "severity": "medium",
                "before": draft_title,
                "after": live_title,
            }
        )

    d_links = extract_links(draft_body)
    l_links = extract_links(live_body)
    if len(d_links) != len(l_links):
        deltas.append(
            {
                "type": "link_count_changed",
                "severity": "high",
                "before": len(d_links),
                "after": len(l_links),
            }
        )
    else:
        for i, (dl, ll) in enumerate(zip(d_links, l_links), start=1):
            if dl["href"] != ll["href"] or _norm_text(dl["text"]) != _norm_text(ll["text"]):
                deltas.append(
                    {
                        "type": "link_target_or_anchor_changed",
                        "severity": "high",
                        "slot": i,
                        "before": dl,
                        "after": ll,
                    }
                )
            if dl["bold"] != ll["bold"] or dl["new_tab"] != ll["new_tab"] or dl["nofollow"] != ll["nofollow"]:
                deltas.append(
                    {
                        "type": "link_policy_shape_changed",
                        "severity": "high",
                        "slot": i,
                        "before": dl,
                        "after": ll,
                    }
                )

    d_imgs = extract_images(draft_body)
    l_imgs = extract_images(live_body)
    if len(d_imgs) != len(l_imgs):
        deltas.append(
            {
                "type": "body_image_count_changed",
                "severity": "medium",
                "before": len(d_imgs),
                "after": len(l_imgs),
            }
        )

    draft_aio = draft.get("aioseo") or {}
    live_excerpt = ((live_post.get("excerpt") or {}).get("raw") or (live_post.get("excerpt") or {}).get("rendered") or "").strip()
    if _norm_text(str(draft_aio.get("meta_description", ""))) != _norm_text(live_excerpt):
        deltas.append(
            {
                "type": "meta_description_changed",
                "severity": "low",
                "before": draft_aio.get("meta_description", ""),
                "after": live_excerpt,
            }
        )

    if site == "dcr":
        caps = ((live_post.get("_embedded") or {}).get("wp:featuredmedia") or [])
        # Keep no-op placeholder for future media caption extraction.
        _ = caps

    return deltas


def append_learned_rules(post_id: int, site: str, deltas: List[Dict[str, Any]]) -> Path:
    LEARNED_RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing: List[Dict[str, Any]] = []
    if LEARNED_RULES_FILE.exists():
        try:
            existing = json.loads(LEARNED_RULES_FILE.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []

    severity_map = {"low": "info", "medium": "warning", "high": "critical"}
    ts = datetime.now(timezone.utc).isoformat()

    def _to_str(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return str(v)

    def _inferred_rule(d: Dict[str, Any]) -> str:
        t = d.get("type") or ""
        slot = d.get("slot")
        slot_note = f" (link slot {slot})" if slot is not None else ""
        if t == "title_edit":
            return "Post title was changed between the draft snapshot and the live post."
        if t == "link_count_changed":
            return "The number of hyperlinks in the article body changed."
        if t == "link_target_or_anchor_changed":
            return f"A link's URL or visible anchor text changed{slot_note}."
        if t == "link_policy_shape_changed":
            return f"Link presentation or policy changed (bold, target, or rel){slot_note}."
        if t == "body_image_count_changed":
            return "The number of images in the article body changed."
        if t == "meta_description_changed":
            return "Meta description or excerpt text changed compared to the draft snapshot."
        return f"Detected change: {str(t).replace('_', ' ')}."

    for d in deltas:
        raw_sev = (d.get("severity") or "low").lower()
        existing.append(
            {
                "timestamp": ts,
                "post_id": int(post_id),
                "site": site,
                "field": str(d.get("type", "")),
                "before": _to_str(d.get("before")),
                "after": _to_str(d.get("after")),
                "inferred_rule": _inferred_rule(d),
                "severity": severity_map.get(raw_sev, "info"),
            }
        )

    LEARNED_RULES_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    git_commit_learned_rules(post_id)
    return LEARNED_RULES_FILE


def git_commit_learned_rules(post_id: int) -> None:
    subprocess.run(["git", "add", "data/learned_rules.json"])
    subprocess.run(
        ["git", "commit", "-m", f"learned_rules: add delta for post {post_id}"]
    )
    subprocess.run(["git", "push", "origin", "main"])


def main(argv: List[str]) -> int:
    if len(argv) != 3:
        print("Usage: python3 learn_from_edits.py <post_id> <site>")
        return 2

    try:
        post_id = int(argv[1])
    except ValueError:
        print("post_id must be an integer")
        return 2

    site = (argv[2] or "").strip().lower()
    if site not in SITES:
        print(f"site must be one of: {', '.join(sorted(SITES))}")
        return 2

    draft = load_draft_snapshot(post_id)
    live = fetch_published_post(post_id, site)
    deltas = classify_rule_deltas(draft, live, site)
    out = append_learned_rules(post_id, site, deltas)

    print(
        json.dumps(
            {
                "post_id": post_id,
                "site": site,
                "deltas_found": len(deltas),
                "learned_rules_file": str(out),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
