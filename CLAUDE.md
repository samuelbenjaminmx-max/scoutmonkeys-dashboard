# Scoutmonkeys — `CLAUDE.md` (operator spec)

This repository powers the **Scoutmonkeys** publishing dashboard and CLI pipeline: Google Docs → WordPress **drafts** for **Cultural Daily (`cd`)** and optionally **DCR (`dcr`)**, with **AIOSEO + cd-seo** integration, **Pexels** imagery, **Anthropic** layout planning, **Twilio WhatsApp** draft notifications, and automated **QA** aligned with `QA.md`.

## Authority

1. **`QA.md`** — must-pass checklist before calling a job complete.
2. **This file** — full operating rules for humans and automation.
3. **Live WordPress / plugin behavior** — if production differs, update the docs and `pipeline.py` together.

## Commands

- **CLI publish (primary):**  
  `python pipeline.py "<google doc url>" cd`  
  Optional site: `dcr` (requires `DCR_*` env vars and compatible SEO endpoints).
- **Web dashboard:** `gunicorn app:app` (Railway sets `PORT`; use `Procfile` locally or on deploy).

## Environment variables

| Variable | Purpose |
|----------|---------|
| `WP_URL`, `WP_USER`, `WP_PASS` | Cultural Daily WordPress (application password) |
| `DCR_WP_URL`, `DCR_WP_USER`, `DCR_WP_PASS` | Optional second site |
| `PEXELS_API_KEY` | Pexels search + downloads |
| `ANTHROPIC_API_KEY` | Claude planning (`ANTHROPIC_MODEL` optional, default `claude-sonnet-4-20250514`) |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM` | Twilio WhatsApp (must be real values, not placeholders) |
| `WHATSAPP_TO` | E.g. `whatsapp:+5215549571586` (preferred) |
| `WHATSAPP_PHONE` | Fallback `+5215549571586` if `WHATSAPP_TO` unset |
| `SECRET_KEY` | Flask session signing |
| `DASHBOARD_PASSWORD` | Dashboard login password |
| `OUR_FRIENDS_AUTHOR_ID` | Default `19` (Cultural Daily) |
| `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` | Reserved for future Gmail/Drive OAuth flows |

Restore local `.env` from Railway:

```bash
railway link -p ba9f4134-0013-45b1-ba61-9420071596e7
railway service scoutmonkeys
railway variables --json | python3 -c "import json,sys; d=json.load(sys.stdin); [print(f'{k}={v}') for k,v in d.items() if not k.startswith('RAILWAY_')]"
```

**Never commit `.env` to a public GitHub repository.** Railway remains the source of truth for production secrets.

## Site rules (`cd` / `dcr`)

- **Prefixes:** `CD-…` vs `DCR-…` for attachment titles (`{prefix}-{topic}-hero|social|insert-N`).
- **Cultural Daily hero:** **975 × 250** (≈ **3.9 : 1**). Featured image must be this hero.
- **Cultural Daily social / OG:** **1920 × 1400** target; set via **AIOSEO** + **`cd-seo`** (not as `featured_media`).
- **DCR defaults** (override with `DCR_HERO_W`, etc.): hero **1200 × 675**, social **1200 × 630** unless your theme/plugin contract differs.
- **Title caps:** CD post + SEO titles **60** chars; DCR **65** (see `QA.md`).
- **Resizing:** Prefer `math.ceil` / max guards when implementing custom crops so output dimensions never land off-by-one (Pillow `ImageOps.fit` is acceptable when it yields exact targets).

## Google Doc ingestion

- The pipeline fetches **public** HTML export:  
  `https://docs.google.com/document/d/{id}/export?format=html`
- The document must be readable without Google OAuth (typically **Anyone with the link can view**). Gmail OAuth env vars are reserved for future Drive/Docs API use.

## Anthropic (Claude)

- Claude turns Doc HTML into structured JSON (topic slug, body HTML, SEO fields, Pexels query).
- Paid / sponsored links in body HTML must be:  
  `<a href="URL" target="_blank"><strong>anchor</strong></a>` — **no** inline `color` styles, **no** `rel="nofollow"` on purchased links.

## Pexels

- Search via Pexels API; pick a candidate whose aspect ratio best matches the site hero ratio (wide banner for CD).
- Hero + social JPEGs are generated from the **same** source frame (social is a separate crop).
- Captions / alts: `Photo: {Photographer} via Pexels` with descriptive alt text (not keyword stuffing). **Hero and social alt text must match.**

## WordPress

- New posts are **`draft`** only.
- **Author:** `OUR_FRIENDS_AUTHOR_ID` (default **19** on CD).
- **Categories:** resolved automatically (`our-friends` slug first, then search); adjust `pipeline.resolve_default_category` if your taxonomy differs.

## AIOSEO + `cd-seo` sequence

After the post exists:

1. `POST /wp-json/aioseo/v1/post` — set title/description/OG custom image URL / keyphrases.
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

- `pipeline.verify_post(...)` encodes the **`QA.md` checklist** (20 checks on CD). Do not mark a publish as “complete” if QA fails.
- WhatsApp is sent when the draft is saved **after** the QA step runs (see `pipeline.run`); fix Twilio placeholders on Railway for real sends.

## DCR caveat

`verify_post` and SEO helpers assume the **`cd-seo`** read/update endpoints exist. If DCR does not ship those plugins, run **`cd` only** or extend the pipeline for that site’s SEO API.
