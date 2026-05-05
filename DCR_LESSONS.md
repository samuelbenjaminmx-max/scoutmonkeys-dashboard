# DCReport (DCR) — lessons ledger

**Append-only.** Do not delete or replace this file. New material is always appended after the latest `---` block.

When QA fails on a DCR pipeline run, `pipeline.dcr_log_lesson(...)` records the failure here automatically.

---

## Baseline lessons (operator + automation)

Captured when this ledger was introduced (permanent reference for DCReport publishing).

- **Hero image:** If the client supplies a hero in the Google Doc, it must **not** remain duplicated in the article body — strip by URL match, fingerprint, and post-upload WordPress hero URL.
- **SEO label lines:** Strip lines such as `SEO Title:` and `Meta Description:` (and related metadata blocks) from the body before publish; they must never appear as article content.
- **Yoast vs AIOSEO / cd-seo:** DCR uses **Yoast** (`_push_yoast_seo`). The **`cd-seo`** REST plugin is typically **absent** on dcreport.org — a **404 on cd-seo read is expected**, not a site outage. QA treats cd-seo-dependent OG checks as **DCR-specific / N/A** and uses Yoast post meta + AIOSEO GET where applicable.
- **Categories:** Default to **Check This Out**; never **Featured Story**; add vertical categories (e.g. casino, betting, CBD, crypto) only when copy clearly matches.
- **Focus keyphrase:** Must be **exactly one word**, present in the post title (H1) and in the meta description.
- **Body links:** Every sponsored body link must be **bold** (`<strong>`) and **`target="_blank"`** (dofollow — not the italic `photo:` credit links).
- **Body images:** Centered figures at **814×532** (site `hero_w` × `hero_h`), descriptive **alt** text, captions exactly **`photo: [Name] via [Platform]`** — italic, linked to source, new tab, not bold.
