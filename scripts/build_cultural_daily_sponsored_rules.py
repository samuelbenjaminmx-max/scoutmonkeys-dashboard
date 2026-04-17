#!/usr/bin/env python3
"""
Build cultural_daily_sponsored_rules.md from data/our_friends_audit.json (audit output).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    data_path = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "data" / "our_friends_audit.json"
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else root / "cultural_daily_sponsored_rules.md"
    data = json.loads(data_path.read_text())
    summary = data.get("summary") or {}
    hero_counts = summary.get("hero_type_counts") or {}
    flag_counts = summary.get("flag_counts") or {}
    n = data.get("post_count", 0)
    note = data.get("note", "")
    iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def fmt_counts(d: dict, limit: int = 25) -> str:
        lines = []
        for i, (k, v) in enumerate(sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))):
            if i >= limit:
                lines.append(f"| _(+{len(d) - limit} more rows omitted)_ | |")
                break
            lines.append(f"| `{k}` | {v} |")
        return "\n".join(lines) if lines else "| _(none)_ | |"

    md = f"""# Cultural Daily — Canonical Sponsored Content Rules Engine

_Generated: {iso} from `data/our_friends_audit.json` (Our Friends corpus audit)._

This document is the **normative rules engine** for Cultural Daily **sponsored / advertorial** HTML produced by the Scoutmonkeys pipeline. Historical “Our Friends” posts are **empirical reference only**; inconsistencies are summarized from live data below and must not override `CLAUDE.md` / `QA.md`.

## Authority & precedence

1. **`CLAUDE.md` / `QA.md`** — editorial and QA contracts (source of truth for what “must pass”).
2. **This file** — corpus-informed rules: how to classify patterns, REST caveats, and aggregate drift from author **{data.get("author_id", 19)}** published posts.
3. **Live WordPress** — when production behavior diverges, update docs and `pipeline.py` together.

## Corpus note

{note or "_No note field in audit JSON._"}

- **Site:** `{data.get("wp_url", "")}`
- **Posts analyzed:** **{n}**
- **Unique featured media objects fetched:** **{summary.get("unique_featured_media", "—")}**
- **Posts with ≥1 inconsistency flag:** **{summary.get("posts_with_any_flag", "—")}**

## REST API — data collection rules

- **Do not** rely on `GET /wp-json/wp/v2/posts?author=19` on Cultural Daily — it returns **HTTP 500** (server/plugin issue).
- **Do** paginate `status=publish` and **filter `author == 19` client-side** (see `scripts/audit_our_friends_posts.py`).

## Hero image (featured media) — empirical distribution

Canonical target remains **975 × 250** (see `QA.md`). Counts below are **attachment dimensions** from featured media, classified heuristically:

| Type label | Count |
|------------|------:|
{fmt_counts(hero_counts)}

## Inconsistency flags — empirical counts

These flags mark divergence from the **pipeline / QA contract**, not “votes” for a new contract:

| Flag | Count |
|------|------:|
{fmt_counts(flag_counts)}

## Canonical rules (unchanged contract)

### Hero (CD)

- Dimensions: **975 × 250**; WordPress **`featured_media`** references this hero only.
- Attachment title pattern: **`CD-{{topic-slug}}-hero`**.
- Alt: descriptive sentence; caption `Photo: {{Name}} via Pexels`.

### Social / OG

- Target **1920 × 1400**; set via **AIOSEO** + **`cd-seo`** (not `featured_media`).
- Title `CD-{{topic-slug}}-social`; social alt **matches** hero alt.

### Citation → `<hr />` → donation

- Citation: `<p><em><a href="https://www.pexels.com/@…">Photo: … via Pexels</a></em></p>` (italic + profile link, **not** bold).
- **`<hr />`** immediately after citation; **no** `<!--nextpage-->`.
- Donation CTA after the rule (CD canonical copy in `pipeline.donation_html_for` / `CLAUDE.md`).

### Paid links

- `<a href="…" target="_blank"><strong>…</strong></a>` — **no** `rel="nofollow"` on purchased links; **no** inline `color` styles.

## Re-run audit + regenerate this file

```bash
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
mkdir -p data
AUDIT_JSON_OUT=data/our_friends_audit.json python3 scripts/audit_our_friends_posts.py > /dev/null
python3 scripts/build_cultural_daily_sponsored_rules.py data/our_friends_audit.json cultural_daily_sponsored_rules.md
```

## Related docs

- `sponsored_content_edge_cases.md` — _(add manually or extend generator if you maintain that file)_.
- `cultural_daily_sponsored_validation_checklist.md` — _(optional companion checklist)_.
"""
    out_path.write_text(md)
    print(f"Wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
