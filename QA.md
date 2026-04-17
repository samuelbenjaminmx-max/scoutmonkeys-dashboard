# Scoutmonkeys — `QA.md` (must-pass checklist)

Use this list before you call a publish **complete**. The automated `pipeline.verify_post()` on Cultural Daily mirrors these checks.

## Identity & workflow

- [ ] Post remains **`draft`** unless there is explicit separate approval to publish.
- [ ] WordPress **author** matches the sponsored byline policy (`OUR_FRIENDS_AUTHOR_ID`, default **19** on CD).
- [ ] **Commercial / paid links** follow the bold-anchor pattern (see `CLAUDE.md`).

## Images

### Hero (featured / banner)

- [ ] **CD:** hero file is exactly **975 × 250** pixels.
- [ ] **DCR:** hero matches configured dimensions (defaults **1200 × 675** unless overridden).
- [ ] Hero is attached as WordPress **`featured_media`** (not the social image).
- [ ] Attachment **title** matches `{PREFIX}-{topic}-hero`.
- [ ] **Alt text** is a descriptive sentence (not bare keywords, not copied SEO title).
- [ ] **Caption** begins with `Photo:` (colon) and credits Pexels properly.

### Social / OG

- [ ] **CD:** social image target **1920 × 1400** (see `CLAUDE.md` for server resize caveats).
- [ ] **DCR:** social matches configured dimensions (defaults **1200 × 630**).
- [ ] Social image is **not** the same asset as the hero.
- [ ] Attachment **title** matches `{PREFIX}-{topic}-social`.
- [ ] **Social alt text** matches **hero alt text** exactly.
- [ ] Social caption starts with `Photo:`.
- [ ] **AIOSEO** shows a **custom** OG image URL, and **`cd-seo`** read-back reflects **`og_image_url`** / postmeta parity (CD).

### Inline images (if any)

- [ ] Titles follow `{PREFIX}-{topic}-insert-1`, `insert-2`, …
- [ ] Same alt + caption discipline as hero/social.

## Links

- [ ] Purchased links use **`<a … target="_blank\"><strong>…</strong></a>`** with **no** `rel="nofollow"`.
- [ ] **No inline `color:` styles** on paid anchors (theme CSS owns color).

## Photo citation & page structure

- [ ] Citation is **italic + hyperlink** to the photographer **profile** on Pexels, **not** bold.
- [ ] Citation **not** wrapped as `<strong>Photo:…`.
- [ ] **`<!--nextpage-->`** does **not** appear.
- [ ] **`<hr />`** exists after the citation block.
- [ ] **Donation** block is present and includes **`CLICK HERE TO DONATE`** (CD canonical copy — see `CLAUDE.md`).
- [ ] **Order:** last `Photo:` credit → `<hr />` → donation block.

## SEO

- [ ] **Focus keyphrase** set and sensible.
- [ ] **SEO score** tooling (if used) reports **≥ 80** when applicable.
- [ ] **Meta description** ≤ **160** characters.
- [ ] **Post title** length: **CD ≤ 60**, **DCR ≤ 65** characters.
- [ ] **SEO title** follows the same per-site cap as the post title check above.

## Notifications

- [ ] **Twilio** environment variables are real (not textual placeholders like `TWILIO_ACCOUNT_SID`).
- [ ] WhatsApp recipient (`WHATSAPP_TO` or `WHATSAPP_PHONE`) is correct for the editor receiving alerts.

## Sign-off

| Role | Name | Date |
|------|------|------|
| Editor | | |
| Tech QA | | |
