# Cultural Daily — corpus vs automation (operator guide)

This document answers: **“How do published Our Friends articles look, and what does the pipeline enforce so I do not have to micromanage each draft?”**

## Two sources of truth

| Artifact | What it is | How to refresh |
|----------|------------|------------------|
| **`data/our_friends_audit.json`** | Per-post flags (links, headings, `cite_pexels`, etc.) for **published** posts by `OUR_FRIENDS_AUTHOR_ID` (default **19**). **No raw HTML.** | `AUDIT_JSON_OUT=data/our_friends_audit.json python3 scripts/audit_our_friends_posts.py` |
| **`data/audit_format_profile.json`** | **HTML patterns** from the same cohort: tag counts, H2 skeletons, spacing gaps, `thresholds` for `format_to_audit_standard()`. | `python3 scripts/build_audit_format_profile.py` (needs `WP_*` credentials; fetches `content.rendered` from REST). |
| **`data/gdoc_intake_profile.json`** | **Input-side** metrics on Google Doc export HTML (same feature vector as above), plus **per-doc min/max/mean bands** over your URL list. Drives soft warnings in the pipeline scorecard. | `python3 scripts/build_gdoc_intake_profile.py` (no WP; needs reachable Doc URLs — default list `data/training_docs.txt`). Optional: `GDOCS_MAX_URLS`, `--max N`. |
| **`cultural_daily_sponsored_rules.md`** | Normative rules + aggregates generated from the audit JSON. | `python3 scripts/build_cultural_daily_sponsored_rules.py data/our_friends_audit.json cultural_daily_sponsored_rules.md` |

Quick summary of what the corpus favors (from `audit_format_profile.json`):

- Mostly **single newline** between tags (`inter_tag_gap_single` dominates; double gaps are rare).
- Heavy use of **`<p>`**, **`<span>`**, **`<h2>`**, lists, **`<strong>`**, **`<a>`** — see `top_20_patterns` and `top_tag_counts` in the JSON.
- **~11.6%** of H2s use leading ordinals in the raw HTML; thresholds may **strip** leading `1.` / `2)`-style markers to match the dominant non-numbered style.

### Shared metrics code

- **`corpus_html_features.py`** — `analyze_html()`, `metrics_snapshot()`, `extract_gdoc_body_inner()` (one definition of “what we measure”).
- **`corpus_compare.py`** — `compare_doc_to_corpora(html)` loads `gdoc_intake_profile.json` + `audit_format_profile.json` and returns **`warnings`** (soft) plus numeric **`features`**.

On each **`python pipeline.py "<gdoc>" cd`** run, after the Doc body is extracted, the pipeline prints **`[1c] Corpus scorecard`** when warnings exist (non-blocking).

## What the pipeline does automatically (CD)

You should **not** hand-fix these if you run the normal CLI or `remediate-latest`:

1. **Body source:** Article HTML from the Google Doc export (not from the LLM), then CD-only transforms.
2. **Sponsored links:** Dofollow, `target="_blank"`, inner **`<strong>`** on body `http(s)` links (see `canonicalize_body_http_links_cd`).
3. **Machine tail:** Citation → `<hr />` → donation CTA order (when the template applies).
4. **Hero / social:** Exact **975×250** hero as featured image; **1920×1400** social JPEG; titles **`CD-{topic}-hero`** / **`CD-{topic}-social`**; OG image points at **social**, not hero.
5. **Inline images:** Dedupe by URL; rehost as **`CD-Insert1`**, **`CD-Insert2`**, …; **centered** figure + WordPress **`wp-image-{id}`** / **`aligncenter`**; sync **attachment `alt_text`** from the `<img alt>` where possible.
6. **Audit-shaped HTML:** `format_to_audit_standard()` uses **`data/audit_format_profile.json`** (embedded `html_format_profile` in the audit JSON is optional fallback).
7. **SEO stack:** **`cd-seo/v1/update`** persists meta + OG; AIOSEO POST uses **`postId`** query param (and path fallback) so “Post ID is missing” does not occur when the install expects it.

## What still needs human judgment

- **Google Doc structure:** Verbatim H1, client hero present or Pexels fallback, sponsored links in the Doc.
- **Editor quirks:** If WordPress downscales huge JPEGs, hosting may need `BIG_IMAGE_SIZE_THRESHOLD` (see `CLAUDE.md`).
- **Media library cruft:** Duplicate uploads from older runs are **not** bulk-deleted by the pipeline; remove orphans in **Media** if needed.

## Commands (minimal micromanagement workflow)

```bash
# New draft from Doc
python3 pipeline.py "<google doc url>" cd

# Repair latest CD draft (CRITICAL_RULES): category, SEO, social size, body HTML sync, etc.
python3 pipeline.py remediate-latest cd
```

**Optional:** `REMEDIATE_SKIP_BODY_POST=1` skips rewriting post content (only for debugging). Default is to **always** sync the processed body on remediate so the block editor matches REST.

## Printed audit summary (local)

```bash
python3 scripts/print_cd_audit_summary.py
```

Prints thresholds and top patterns from `data/audit_format_profile.json` (no network).

After you build **`data/gdoc_intake_profile.json`**, soft warnings compare the **current Doc** to the **min/max/mean bands** from your URL corpus (e.g. unusually many H2s or images vs typical GDocs you process).
