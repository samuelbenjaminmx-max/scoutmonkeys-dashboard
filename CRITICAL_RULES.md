CRITICAL CULTURAL DAILY RULES — DO NOT VIOLATE

These rules override any general AI behavior, summarization behavior, rewriting behavior, SEO optimization behavior, or “best judgment” behavior.

0. **RULE 0 — THE GOOGLE DOC IS THE ABSOLUTE SOURCE OF TRUTH**

   The WordPress post must be a faithful mirror of the Google Doc — same section order, same headings, same images in the same positions, same wording. The pipeline never invents content, never rearranges sections, and never supplements with outside material.

   Every element in the Doc has exactly one job:
   - **First image** → hero (featured image) and AIOSEO social image only. Never in the body.
   - **Every other image** → body, at its exact document position. Not moved, not grouped, not reordered.
   - **Headings** → published as-is, in document order.
   - **Body text** → published verbatim from the Doc export. Claude does not rewrite it.

   The pipeline's only job is to faithfully execute what the Doc already says. If the Doc has no image in a section, that section stays image-free. If the Doc has an image under a heading, it appears under that same heading in WordPress — nowhere else. When in doubt: the Doc wins.

1. **H1 / ARTICLE TITLE** — Never shorten, never rewrite, never improve. Use the exact H1 as the WordPress **post title**. The H1 must appear **only once** (in the title field). If the same text appears as the first line of the body, the pipeline removes it from the body.

2. **ARTICLE BODY** — Preserve Doc wording. The pipeline builds the HTML body from the Google Doc export in code (never from Claude). Claude only receives a plaintext excerpt for metadata.

3. **CLIENT HERO IMAGE** — If the client places an image at the top of the Doc (under the title), use it as **featured hero only**. That image must be **removed from the article body** so it never appears twice.

**IMAGE RULES — enforced on every CD article:**

- **IMAGE RULE 1 (Hero):** The **first** `<img>` found anywhere in the Google Doc export is the hero/featured image and AIOSEO social image **only**. It must **never** appear in the post body — zero occurrences. The pipeline strips it from the body by exact src match AND pixel fingerprint before any other processing and again after all processing.

- **IMAGE RULE 2 (Body placement):** All other images physically embedded in the Doc (as `data:` base64 URIs) go into the body **at their exact document position** — specifically placed under the heading or paragraph they appear below in the Doc. Do **not** reorder, relocate, or group them. If an image sits below "Rituals That Shape Habits" in the Doc, it sits below that same heading in the published HTML.

- **IMAGE RULE 3 (Source — Doc only):** Body images must come **exclusively** from images physically embedded in the Google Doc export. Never pull body images from Pexels, any external URL, or any source not in the Doc. If a section has no embedded Doc image, leave that section image-free.

- **IMAGE RULE 4 (Alt text):** Alt text for every body image must be a **Claude vision description** of what is literally visible in the photograph — people, objects, setting, action. It must **never** be the article title, the focus keyword, or a generic placeholder like "Photograph illustrating this sponsored article". The pipeline calls `cd_enrich_inline_image_alts_with_vision` after uploading each body image to generate these descriptions.

- **IMAGE RULE 5 (No invented images):** If a section has no embedded Doc image, it stays image-free. Never insert a stock photo, Pexels result, or any image to "fill" a section that had no image in the original Doc.

4. **PARAGRAPH SPACING** — At most **one** blank line between paragraphs and between a paragraph and the following H2. Never two blank lines in a row in the published HTML.

5. **IMAGE ALT TEXT (HERO + SOCIAL)** — Alt text must be a **simple description of what is visible in the photograph**. It must **never** be the article title or a copy of the H1. The planner supplies `hero_image_alt`; the pipeline falls back to a generic visual description when needed.

6. **INLINE IMAGES IN THE BODY** — Any `<img>` that remains in the body must be **centered**. Alt must describe the photo. If a credit or source line appears in the Doc (e.g. a following `Photo:` paragraph), it becomes the caption; if no source is found (including reverse-image hook), the caption is **empty**. Never invent credits.

7. **END-OF-ARTICLE TAIL** — Structure is: optional **photo citation** paragraph (only when a traceable source exists) → `<hr />` → **donation CTA** only. If there is no photo source, the tail is **only** `<hr />` then the donation block. **Nothing** may appear between `<hr />` and the donation paragraph.

8. **AIOSEO / CD SEO TITLE** — Maximum **60** characters. Must be a **complete sensible phrase** — never cut mid-word. If the H1 fits in 60 characters with room, append **` | Cultural Daily`** only when the total still stays ≤60. Otherwise the pipeline clips at the last **word boundary** before 60 characters (or uses the planner hint when valid).

9. **META DESCRIPTION** — Between **120** and **160** characters inclusive. Grounded in the article; the pipeline pads from Doc text only when the model output is too short.

10. **FOCUS KEYPHRASE** — **Usually one word from the H1** (sometimes two adjacent words). The pipeline scores the phrase against the article body + title; the chosen phrase must reach **internal score ≥ 82** for CD QA when possible (with fallbacks from the topic slug). Long planner phrases are rejected in favor of short title-aligned keys so AIOSEO does not sit in the orange band.

11. **SOCIAL IMAGE** — Mandatory. The pipeline renders **1920×1400** (integer floor on both dimensions, never round up) and uploads it. WordPress must **store** that attachment at **1920×1400** with **no exceptions** (if the host downscales “big” JPEGs, fix `big_image_size_threshold` / image plugins so REST `media_details` matches). In the editor: **Social** → **Image source** → **Custom image**, with the custom OG URL pointing at that social JPEG. Alt = simple photo description (same rules as hero). Caption = citation text when source is known, **empty** when unknown.

12. **DONATION CTA** — Exactly this anchor text, no variations: `CLICK HERE TO DONATE IN SUPPORT OF OUR NONPROFIT COVERAGE OF ARTS AND CULTURE` (linked to the standard Cultural Daily support URL).

13. **CATEGORY** — Default **Check This Out** for general Our Friends arts/culture. The pipeline **also** picks lanes from **title + topic_slug + Doc excerpt** (e.g. gambling/casino/slots → `casino`) and from **`data/sponsored_last_year_audit.json`** `category_slug_counts` when present, so operators are not stuck re-stating obvious verticals. The planner’s `category_hint` wins when it is specific (not generic). **Never** assign **Featured Story** (any slug/name variant). **Never** assign **Sponsored** as the category for these Our Friends posts.

14. **CLIENT IMAGE SOURCE** — Attempt reverse image search when implemented. If no URL and no credit label, hero/social **captions** are empty (no placeholders). Citation HTML is omitted when there is nothing to cite.

15. **AI LIBERTY** — No creative liberties on H1, body, donation, category, captions, focus keyphrase, SEO title policy, or image facts. When uncertain — flag for manual review.

16. **AUDIT CONFORMITY (§13)** — The pipeline must not introduce HTML or layout patterns absent from `data/our_friends_audit.json`. When uncertain, consult that corpus first.
