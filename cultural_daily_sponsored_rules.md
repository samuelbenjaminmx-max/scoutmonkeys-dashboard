# Cultural Daily — Canonical Sponsored Content Rules Engine

_Generated: 2026-04-18 13:00 UTC from `data/our_friends_audit.json` (Our Friends corpus audit)._

This document is the **normative rules engine** for Cultural Daily **sponsored / advertorial** HTML produced by the Scoutmonkeys pipeline. Historical “Our Friends” posts are **empirical reference only**; inconsistencies are summarized from live data below and must not override `CLAUDE.md` / `QA.md`.

## Authority & precedence

1. **`CLAUDE.md` / `QA.md`** — editorial and QA contracts (source of truth for what “must pass”).
2. **This file** — corpus-informed rules: how to classify patterns, REST caveats, and aggregate drift from author **19** published posts.
3. **Live WordPress** — when production behavior diverges, update docs and `pipeline.py` together.

## Absolute pipeline rules (sponsored-only contract)

These two rules override informal wording elsewhere and apply to **every** Scoutmonkeys → Cultural Daily article:

1. **Sponsored-only body links** — Every article is paid placement copy. Every outbound `http(s):` link in the **article body** is a **commercial dofollow** anchor. Required shape:

   `<a href="…" target="_blank"><strong>…</strong></a>`

   **No** `rel="nofollow"` on those anchors. **No** inline `color:` styles on anchors. There is **no** “editorial link” exception for body copy.

   **Tail carve-out:** The machine-appended Pexels **photographer profile** citation uses italic + `rel="nofollow noopener"` on that single attribution line only (`pipeline.py` / `QA.md`).

2. **Hero image is mandatory** — WordPress **`featured_media`** must always be the pipeline-resized **Pexels** hero at **975 × 250** (CD). If the Google Doc includes **no** client hero, the pipeline **still** runs Pexels search with **fallback queries** until a usable frame is found. A post must **never** ship without a hero.

**Parser note:** `doc_parser` inventories every http(s) `<a>` with the same shape fields (bold, `target`, `nofollow`, …). There is no editorial-vs-paid taxonomy — every body URL is expected to become a paid dofollow anchor in pipeline output.

## Corpus note

WP REST returns HTTP 500 for ?author=19 on this site; published posts were scanned and filtered client-side.

- **Site:** `https://www.culturaldaily.com`
- **Posts analyzed:** **3208**
- **Unique featured media objects fetched:** **3081**
- **Posts with ≥1 inconsistency flag:** **3200**

## REST API — data collection rules

- **Do not** rely on `GET /wp-json/wp/v2/posts?author=19` on Cultural Daily — it returns **HTTP 500** (server/plugin issue).
- **Do** paginate `status=publish` and **filter `author == 19` client-side** (see `scripts/audit_our_friends_posts.py`).

## Hero image (featured media) — empirical distribution

Canonical target remains **975 × 250** (see `QA.md`). Counts below are **attachment dimensions** from featured media, classified heuristically:

| Type label | Count |
|------------|------:|
| `cd_banner_975x250` | 1936 |
| `wide_banner_like_972x250` | 265 |
| `social_landscape_like_640x427` | 114 |
| `social_landscape_like_1920x1280` | 87 |
| `social_landscape_like_2560x1707` | 51 |
| `wide_banner_like_1116x286` | 37 |
| `other_640x426` | 30 |
| `social_landscape_like_600x400` | 25 |
| `other_1280x853` | 22 |
| `social_landscape_like_640x480` | 18 |
| `other_1920x1277` | 12 |
| `social_landscape_like_1920x1440` | 11 |
| `social_landscape_like_600x401` | 10 |
| `other_600x398` | 9 |
| `wide_banner_like_1920x492` | 9 |
| `other_600x394` | 8 |
| `other_600x395` | 8 |
| `other_600x399` | 8 |
| `social_landscape_like_2560x1709` | 8 |
| `other_600x391` | 7 |
| `other_640x425` | 7 |
| `social_landscape_like_2400x1600` | 7 |
| `social_landscape_like_600x402` | 7 |
| `other_1920x1080` | 6 |
| `other_600x397` | 6 |
| _(+283 more rows omitted)_ | |

## Inconsistency flags — empirical counts

These flags mark divergence from the **pipeline / QA contract**, not “votes” for a new contract:

| Flag | Count |
|------|------:|
| `hero_title_not_cd_topic_hero` | 3083 |
| `hero_not_975x250` | 1159 |
| `hero_alt_short_or_empty` | 1067 |
| `missing_donation` | 1066 |
| `missing_hr` | 930 |
| `citation_not_em_a_pexels` | 302 |
| `missing_featured_media` | 113 |

## Canonical rules (unchanged contract)

### Hero (CD)

- Dimensions: **975 × 250**; WordPress **`featured_media`** references this hero only (always sourced via **Pexels** when the client Doc has no usable inline hero).
- Attachment title pattern: **`CD-{topic-slug}-hero`**.
- Alt: descriptive sentence; caption `Photo: {Name} via Pexels`.

### Social / OG

- Target **1920 × 1400**; set via **AIOSEO** + **`cd-seo`** (not `featured_media`).
- Title `CD-{topic-slug}-social`; social alt **matches** hero alt.

### Citation → `<hr />` → donation

- Citation: `<p><em><a href="https://www.pexels.com/@…">Photo: … via Pexels</a></em></p>` (italic + profile link, **not** bold).
- **`<hr />`** immediately after citation; **no** `<!--nextpage-->`.
- Donation CTA after the rule (CD canonical copy in `pipeline.donation_html_for` / `CLAUDE.md`).

### Paid links (sponsored-only site)

- **Every** outbound body `http(s):` link is a paid dofollow anchor: `<a href="…" target="_blank"><strong>…</strong></a>` — **no** `rel="nofollow"`; **no** inline `color` styles; **no** editorial/plain treatment.

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
