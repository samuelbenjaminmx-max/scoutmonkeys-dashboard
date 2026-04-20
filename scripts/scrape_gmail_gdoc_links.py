#!/usr/bin/env python3
"""
scripts/scrape_gmail_gdoc_links.py

Search Gmail for emails containing Google Doc links from the last 6 months.
Processes in batches of 50 with 5-second pauses. Saves progress to
data/discovery_progress.json so the run is fully resumable if interrupted.

Outputs unique doc URLs to data/discovered_gdoc_urls.txt (one per line),
then merges them into data/training_docs.txt (deduped).

Usage:
    python3 scripts/scrape_gmail_gdoc_links.py

Required env vars: GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
PROGRESS_JSON = REPO_ROOT / "data" / "discovery_progress.json"
DISCOVERED_TXT = REPO_ROOT / "data" / "discovered_gdoc_urls.txt"
TRAINING_DOCS = REPO_ROOT / "data" / "training_docs.txt"

BATCH_SIZE = 50
BATCH_PAUSE = 5.0  # seconds between Gmail API batches
GMAIL_API = "https://www.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# Six months back from today (approximate)
AFTER_DATE = "2025/10/20"

GDOC_RE = re.compile(
    r"https://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)",
    re.I,
)


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

CLIENT_ID = os.environ.get("GMAIL_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("GMAIL_REFRESH_TOKEN", "")


# ── OAuth token refresh ───────────────────────────────────────────────────────

_access_token: str = ""
_token_expiry: float = 0.0


def get_access_token() -> str:
    global _access_token, _token_expiry
    if _access_token and time.time() < _token_expiry - 60:
        return _access_token
    r = requests.post(
        TOKEN_URL,
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    _access_token = data["access_token"]
    _token_expiry = time.time() + int(data.get("expires_in", 3600))
    return _access_token


def _auth_header() -> dict:
    return {"Authorization": f"Bearer {get_access_token()}"}


# ── Gmail API helpers ─────────────────────────────────────────────────────────

def list_message_ids(page_token: Optional[str] = None) -> tuple[list[str], Optional[str]]:
    """Return one page of message IDs matching the search query."""
    params: dict = {
        "q": f"docs.google.com/document after:{AFTER_DATE}",
        "maxResults": BATCH_SIZE,
    }
    if page_token:
        params["pageToken"] = page_token
    r = requests.get(
        f"{GMAIL_API}/messages",
        headers=_auth_header(),
        params=params,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    ids = [m["id"] for m in data.get("messages", [])]
    next_token = data.get("nextPageToken")
    return ids, next_token


def fetch_message_urls(msg_id: str) -> list[str]:
    """Fetch a single Gmail message and extract all Google Doc URLs."""
    try:
        r = requests.get(
            f"{GMAIL_API}/messages/{msg_id}",
            headers=_auth_header(),
            params={"format": "full", "fields": "payload,snippet"},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"    [warn] message {msg_id}: {e}")
        return []

    urls: list[str] = []

    # Extract from snippet
    snippet = data.get("snippet", "")
    for m in GDOC_RE.finditer(snippet):
        urls.append(f"https://docs.google.com/document/d/{m.group(1)}/edit")

    # Walk MIME parts
    def _walk(part: dict) -> None:
        body = part.get("body") or {}
        raw = body.get("data") or ""
        if raw:
            import base64
            try:
                text = base64.urlsafe_b64decode(raw + "==").decode("utf-8", errors="replace")
                for m in GDOC_RE.finditer(text):
                    urls.append(f"https://docs.google.com/document/d/{m.group(1)}/edit")
            except Exception:
                pass
        for sub in part.get("parts") or []:
            _walk(sub)

    payload = data.get("payload") or {}
    _walk(payload)

    return list(set(urls))


# ── progress helpers ──────────────────────────────────────────────────────────

def load_progress() -> dict:
    if PROGRESS_JSON.is_file():
        try:
            return json.loads(PROGRESS_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "next_page_token": None,
        "processed_message_ids": [],
        "found_urls": [],
        "batches_done": 0,
        "done": False,
    }


def save_progress(progress: dict) -> None:
    PROGRESS_JSON.write_text(json.dumps(progress, indent=2, ensure_ascii=False), encoding="utf-8")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if not CLIENT_ID or not CLIENT_SECRET or not REFRESH_TOKEN:
        print("ERROR: GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN must be set.")
        sys.exit(1)

    progress = load_progress()

    if progress.get("done"):
        print("Discovery already complete. Delete data/discovery_progress.json to restart.")
        print(f"Found URLs: {len(progress.get('found_urls', []))}")
    else:
        processed_ids: set[str] = set(progress.get("processed_message_ids") or [])
        found_urls: set[str] = set(progress.get("found_urls") or [])
        page_token: Optional[str] = progress.get("next_page_token")
        batches_done: int = int(progress.get("batches_done") or 0)

        print(f"Resuming from batch {batches_done + 1}. "
              f"Already processed {len(processed_ids)} messages, "
              f"{len(found_urls)} URLs found so far.\n")

        try:
            while True:
                print(f"Batch {batches_done + 1}: listing messages (page_token={page_token!r})…")
                try:
                    msg_ids, next_page_token = list_message_ids(page_token)
                except Exception as e:
                    print(f"  [error] Gmail list failed: {e}")
                    break

                if not msg_ids:
                    print("  No more messages.")
                    break

                new_ids = [mid for mid in msg_ids if mid not in processed_ids]
                print(f"  {len(msg_ids)} messages in page, {len(new_ids)} new to fetch")

                for mid in new_ids:
                    urls = fetch_message_urls(mid)
                    found_urls.update(urls)
                    processed_ids.add(mid)
                    if urls:
                        print(f"    {mid}: {len(urls)} URL(s) → {urls[0][:80]}")

                batches_done += 1
                progress.update({
                    "next_page_token": next_page_token,
                    "processed_message_ids": list(processed_ids),
                    "found_urls": sorted(found_urls),
                    "batches_done": batches_done,
                    "done": next_page_token is None,
                })
                save_progress(progress)

                if next_page_token is None:
                    print("  Reached end of search results.")
                    break

                print(f"  Saved progress. Pausing {BATCH_PAUSE}s before next batch…")
                time.sleep(BATCH_PAUSE)

        except KeyboardInterrupt:
            print("\nInterrupted. Progress saved — re-run to continue.")

        found_urls = set(progress.get("found_urls") or [])
        print(f"\nDiscovery finished. Total unique Google Doc URLs: {len(found_urls)}")

    found_urls = set(progress.get("found_urls") or [])

    # Write discovered_gdoc_urls.txt
    DISCOVERED_TXT.write_text(
        "\n".join(sorted(found_urls)) + ("\n" if found_urls else ""),
        encoding="utf-8",
    )
    print(f"Wrote {len(found_urls)} URLs → {DISCOVERED_TXT.name}")

    # Merge into training_docs.txt (deduped)
    existing: set[str] = set()
    if TRAINING_DOCS.is_file():
        for line in TRAINING_DOCS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                # Normalise: strip trailing /edit etc. to just the doc ID
                m = re.search(r"/document/d/([a-zA-Z0-9_-]+)", line)
                if m:
                    existing.add(m.group(1))

    new_urls = [
        u for u in sorted(found_urls)
        if (m := re.search(r"/document/d/([a-zA-Z0-9_-]+)", u)) and m.group(1) not in existing
    ]
    if new_urls:
        with TRAINING_DOCS.open("a", encoding="utf-8") as f:
            f.write("\n# --- Gmail discovery ---\n")
            for u in new_urls:
                f.write(u + "\n")
        print(f"Merged {len(new_urls)} new URLs into {TRAINING_DOCS.name}")
    else:
        print("No new URLs to add to training_docs.txt (all already present).")

    print("\nNext step: run  python3 scripts/build_matched_pairs.py  to match to WordPress.")


if __name__ == "__main__":
    main()
