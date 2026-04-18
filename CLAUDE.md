# Scoutmonkeys — `CLAUDE.md` (operator spec)

This repository powers the **Scoutmonkeys** publishing dashboard and CLI pipeline: Google Docs → WordPress **drafts** for **Cultural Daily (`cd`)** and optionally **DCR (`dcr`)**, with **AIOSEO + cd-seo** integration, **Pexels** imagery, **Anthropic** layout planning, **Twilio WhatsApp** draft notifications, and automated **QA** aligned with `QA.md`.

## Authority

0. **`CRITICAL_RULES.md`** — when this file exists in the repository, it **overrides** every other instruction (including this file, `QA.md`, `cultural_daily_sponsored_rules.md`, and default Claude behavior). The pipeline loads it into Anthropic context and branches on verbatim H1, faithful body copy, client hero images (no Pexels replacement when a Doc image exists), **Check This Out** category on CD, compact focus keyword, and stricter QA hooks. **§12 AUDIT CONFORMITY:** output must not introduce HTML/formatting/content patterns that are absent from the audited Our Friends corpus in **`data/our_friends_audit.json`** (3,208 posts when last generated). When unsure, consult that JSON (or regenerate it via `scripts/audit_our_friends_posts.py`) before inventing structure; if a pattern is not evidenced there, do not use it.
1. **`QA.md`** — must-pass checklist before calling a job complete (subordinate to `CRITICAL_RULES.md` when both apply).
2. **This file** — full operating rules for humans and automation.
3. **Live WordPress / plugin behavior** — if production differs, update the docs and `pipeline.py` together.

## Commands

- **CLI publish (primary):**  
  `python pipeline.py "<google doc url>" cd`  
  Optional site: `dcr` (requires `DCR_*` env vars and compatible SEO endpoints).
- **Repair latest CD draft (CRITICAL rules):**  
  `python pipeline.py remediate-latest cd`  
  Loads `REPO_ROOT/.env` if needed, then forces **Check This Out**, compacts the focus keyphrase, aligns SEO title to the post title, **always re-saves** the processed article body to WordPress (inline **`CD-InsertN`**, dedupe, centered figures, audit formatting — avoids editor/REST drift), patches **social** attachment **alt_text**, resolves **`CD-{topic}-social`** for **AIOSEO / cd-seo** OG (not the hero), and re-uploads the social JPEG at **1920×1400** when that attachment is missing or the wrong size. Set **`REMEDIATE_SKIP_BODY_POST=1`** only to skip the body POST (debugging). Exits non-zero if `verify_post` still fails (e.g. body link shape).  
  **Corpus + automation overview:** `docs/CD_CORPUS_AND_AUTOMATION.md` · **Published HTML profile:** `data/audit_format_profile.json` (`scripts/build_audit_format_profile.py`) · **Google Doc intake profile:** `data/gdoc_intake_profile.json` (`scripts/build_gdoc_intake_profile.py`, URL list e.g. `data/training_docs.txt`) · **Quick print:** `python3 scripts/print_cd_audit_summary.py` · Each CD run logs a **corpus scorecard** (`[1c]`) when the Doc differs from those profiles.
- **Web dashboard:** `gunicorn app:app` (Railway sets `PORT`; use `Procfile` locally or on deploy).
- **Doc intake parse (batch):**  
  `python doc_parser.py --batch data/training_docs.txt --out data/training_parse_report.json`  
  Every `pipeline.py` run parses the export HTML first (`doc_parser.parse_google_doc_intake`) and passes structured JSON to Claude alongside `cultural_daily_sponsored_rules.md`. **Link rows are inventory + shape hints only** (href, bold, `target`, `nofollow`) — not an editorial/paid taxonomy.  
  The batch fetch retries transient Google **500 / 502 / 503 / 429** responses; **401** (private) and **410** (removed) docs stay in `training_parse_report.json` → `errors`.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `WP_URL`, `WP_USER`, `WP_PASS` | Cultural Daily WordPress (application password) |
| `DCR_WP_URL`, `DCR_WP_USER`, `DCR_WP_PASS` | Optional second site |
| `PEXELS_API_KEY` | Pexels search + downloads |
| `ANTHROPIC_API_KEY` | Claude planning (`ANTHROPIC_MODEL` optional, default `claude-sonnet-4-6`) |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM` | Twilio WhatsApp (must be real values, not placeholders) |
| `WHATSAPP_TO` | E.g. `whatsapp:+5215549571586` (preferred) |
| `WHATSAPP_PHONE` | Fallback `+5215549571586` if `WHATSAPP_TO` unset |
| `SECRET_KEY` | Flask session signing |
| `DASHBOARD_PASSWORD` | Dashboard login password |
| `OUR_FRIENDS_AUTHOR_ID` | Default `19` (Cultural Daily) |
| `REMEDIATE_SKIP_BODY_POST` | If `1` / `true`, `remediate-latest cd` skips updating post `content` (default: body is always synced) |
| `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` | Reserved for future Gmail/Drive OAuth flows |

Restore local `.env` from Railway:

```bash
railway link -p ba9f4134-0013-45b1-ba61-9420071596e7
railway service scoutmonkeys
railway variables --json | python3 -c "import json,sys; d=json.load(sys.stdin); [print(f'{k}={v}') for k,v in d.items() if not k.startswith('RAILWAY_')]"
```

**Never commit `.env` to a public GitHub repository.** Railway remains the source of truth for production secrets.

## GitHub push (HTTPS + `gh`)

Remote: `https://github.com/samuelbenjaminmx-max/scoutmonkeys-dashboard.git`  
_(The path `github.com/samuelshemin/…` is not a valid GitHub user or org; the repository was created under the authenticated account instead.)_

1. Install the [GitHub CLI](https://cli.github.com/) (`gh`) if needed (Homebrew: `brew install gh`, or download the macOS `gh` release and add `bin` to your `PATH`).
2. Authenticate over HTTPS (device flow opens the browser):

   ```bash
   gh auth login -h github.com -p https -w
   gh auth setup-git
   ```

3. Push:

   ```bash
   cd ~/Desktop/scoutmonkeys-dashboard
   git remote set-url origin https://github.com/samuelbenjaminmx-max/scoutmonkeys-dashboard.git
   git push -u origin main
   ```

`gh auth setup-git` wires Git’s HTTPS credential helper to `gh`, so `git push` uses your logged-in GitHub account.

## Our Friends audit + `cultural_daily_sponsored_rules.md`

The audit scans **all published posts** and keeps those with `author == OUR_FRIENDS_AUTHOR_ID` (default **19**), because `?author=19` returns HTTP 500 on Cultural Daily.

```bash
mkdir -p data
AUDIT_JSON_OUT=data/our_friends_audit.json python3 scripts/audit_our_friends_posts.py > /dev/null
python3 scripts/build_cultural_daily_sponsored_rules.py data/our_friends_audit.json cultural_daily_sponsored_rules.md
```

**`data/our_friends_audit.json`** is **committed to this repository** (large file; regenerate with credentials locally when refreshing). It is the **empirical ground truth** for **CRITICAL_RULES.md §12 (audit conformity)**: the pipeline and Claude must not emit HTML or content structures that are not evidenced in that corpus. The generated **`cultural_daily_sponsored_rules.md`** is safe to commit: it embeds aggregate counts and the normative rules summary.

**Rolling HTML audit (Our Friends, published):** `python3 scripts/audit_sponsored_last_year.py` fetches posts in a time window (default **365 days**), aggregates final `content.rendered` formatting, writes **`data/sponsored_last_year_audit.json`**, and regenerates the human-readable **`docs/CULTURAL_DAILY_SPONSORED_FORMAT_GUIDE.md`** (“how sponsored posts look on the site lately”). Use it alongside **`data/gdoc_intake_profile.json`** (Google Doc exports) to align inputs and outputs—see **`docs/CD_CORPUS_AND_AUTOMATION.md`**.

Optional: `AUDIT_MAX_POSTS=500` for a faster sample run.

## Site rules (`cd` / `dcr`)

- **Prefixes:** `CD-…` vs `DCR-…` for attachment titles (`{prefix}-{topic}-hero|social|insert-N`).
- **Cultural Daily hero:** **975 × 250** (≈ **3.9 : 1**). Featured image must be this hero.
- **Cultural Daily social / OG:** **1920 × 1400** target; set via **AIOSEO** + **`cd-seo`** (not as `featured_media`). The **custom / checkmarked** OG image in AIOSEO must be the **`CD-{topic}-social`** attachment URL (never the hero).
- **Large uploads (`big_image_size_threshold`):** WordPress may downscale “big” JPEGs on ingest (hosts sometimes cap around **1481×1080** even when the pipeline uploads **1920×1400** bytes). That is a **server** setting, not something `pipeline.py` can override. **Permanent fix:** Todd (or hosting) should raise or disable the threshold in **`wp-config.php`**, e.g. `define('BIG_IMAGE_SIZE_THRESHOLD', 9999);` (or the supported filter / constant pattern your WP version documents — goal: stop automatic downscaling of social JPEGs). Until then, QA may show stored social dimensions below **1920×1400** while filenames and OG selection remain correct.
- **DCR defaults** (override with `DCR_HERO_W`, etc.): hero **1200 × 675**, social **1200 × 630** unless your theme/plugin contract differs.
- **Title caps:** CD post + SEO titles **60** chars; DCR **65** (see `QA.md`).
- **Resizing:** Prefer `math.ceil` / max guards when implementing custom crops so output dimensions never land off-by-one (Pillow `ImageOps.fit` is acceptable when it yields exact targets).

## Google Doc ingestion

- The pipeline fetches **public** HTML export:  
  `https://docs.google.com/document/d/{id}/export?format=html`
- The document must be readable without Google OAuth (typically **Anyone with the link can view**). Gmail OAuth env vars are reserved for future Drive/Docs API use.

## Sponsored paid content (hard rule)

1. **Every article on this site is sponsored paid content.** Every outbound `http(s):` link in the **article body** (before the machine tail) is a **paid dofollow** link — **no exceptions**, **no** separate “editorial” link type, **no** parser classification step:  
   `<a href="URL" target="_blank"><strong>anchor</strong></a>` — **no** `rel="nofollow"`, **no** inline `color` styles.  
   `doc_parser` only **inventories** anchors and records shape hints (`has_strong`, `target_blank`, `has_nofollow`, …) so Claude and QA can enforce the same shape.  
   The **Pexels photographer profile** line in the **machine tail** is the only deliberate `nofollow` anchor (attribution, italic — see **Tail structure**). **Client-supplied photo credits** in the tail are also `nofollow` where linked.
2. **Hero image source:** If **`CRITICAL_RULES.md`** is absent, the default is **Pexels-backed hero**: search Pexels (with fallbacks) and attach a resized hero as `featured_media`. **Never** publish without a hero. **When `CRITICAL_RULES.md` is present**, a **client image in the Doc takes priority** (no Pexels replacement); Pexels is used only when the Doc has **no** usable client image.

## Anthropic (Claude)

- Claude turns Doc HTML into structured JSON (topic slug, body HTML, SEO fields, Pexels query).
- Body links follow the **hard sponsored rule** above — there is no alternate editorial treatment in prompts or parser output.

## Pexels

- Search via Pexels API; pick a candidate whose aspect ratio best matches the site hero ratio (wide banner for CD). If the first query returns nothing usable, the pipeline **retries with broader fallback queries** until a hero is resolved (or fails loudly if Pexels is empty / `PEXELS_API_KEY` missing).
- Hero + social JPEGs are generated from the **same** source frame (social is a separate crop).
- Captions / alts: `Photo: {Photographer} via Pexels` with descriptive alt text (not keyword stuffing). **Hero and social** use the same descriptive alt string from the pipeline when both are generated together; **never** use the article title as image alt text.

## WordPress

- New posts are **`draft`** only.
- **Author:** `OUR_FRIENDS_AUTHOR_ID` (default **19** on CD).
- **Categories:** default pipeline uses `our-friends` / search hints; **when `CRITICAL_RULES.md` exists on CD**, the pipeline uses **`resolve_check_this_out_category`** (must never assign Sponsored for these posts).

## AIOSEO + `cd-seo` sequence

After the post exists:

1. `POST /wp-json/aioseo/v1/post?postId={id}` — set title/description/OG custom image URL / keyphrases (query `postId` required on many installs; path fallback `…/post/{id}` if needed).
2. `POST /wp-json/cd-seo/v1/update` — resolve `og_image_url` + postmeta parity for the Cultural Daily stack.

If either call fails, the pipeline logs a warning; fix credentials, capabilities, or payload shapes before treating SEO as complete.

## Tail structure (sponsored template)

End of HTML (machine order):

1. **Citation:**  
   `<p><em><a href="https://www.pexels.com/@…" target="_blank" rel="nofollow noopener">Photo: … via Pexels</a></em></p>`  
   Italic + link to **photographer profile**, **not** bold.
2. **`<hr />`** immediately after the citation (not `<!--nextpage-->`).
3. **Donation CTA** after the rule — CD uses the canonical block in `pipeline.donation_html_for`.

## QA

- `pipeline.verify_post(...)` encodes the **`QA.md` checklist** (21 checks on CD, plus extra checks when `CRITICAL_RULES.md` is active). Generated hero/social JPEGs are asserted **exact** pixel dimensions in `pipeline.run` before upload. Do not mark a publish as “complete” if QA fails.
- WhatsApp is sent when the draft is saved **after** the QA step runs (see `pipeline.run`); fix Twilio placeholders on Railway for real sends.

## DCR caveat

`verify_post` and SEO helpers assume the **`cd-seo`** read/update endpoints exist. If DCR does not ship those plugins, run **`cd` only** or extend the pipeline for that site’s SEO API.
