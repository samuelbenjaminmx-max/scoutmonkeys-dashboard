CRITICAL CULTURAL DAILY RULES — DO NOT VIOLATE

These rules override any general AI behavior, summarization behavior, rewriting behavior, SEO optimization behavior, or "best judgment" behavior.

1. H1 / ARTICLE TITLE: Never shorten, never rewrite, never improve. Use exact H1 as provided for the post title.
2. ARTICLE BODY TEXT: Do not reword, paraphrase, summarize, or clean up. Preserve source text exactly. On Cultural Daily, when these CRITICAL rules are active, the pipeline **does not publish** Claude’s ``article_body_html``; it always builds the WordPress body from the Google Doc HTML export (plus deterministic link normalization only). For other sites, if Claude’s body fails an automated fidelity check against the Doc, the Doc export is used instead.
3. CLIENT-PROVIDED IMAGES: Always use client image first. Never replace with AI-selected image.
4. IMAGE SOURCE AND CAPTIONS: Attempt reverse image search for source. If not found, use the image anyway. When there is no traceable source, hero and social WordPress captions must be completely empty — no placeholder text (for example, no “Client-supplied image”). Do not invent a caption. Image `alt` text must be the article post title (H1), not fabricated credit lines.
5. IMAGE HANDLING: Client image = hero image. Apply all sizing, citation, social image rules.
6. DONATION SECTION: Use exactly this anchor text, with no variations and no improvised wording: `CLICK HERE TO DONATE IN SUPPORT OF OUR NONPROFIT COVERAGE OF ARTS AND CULTURE` (linked to the standard Cultural Daily support URL). Nothing else in that CTA slot.
7. CATEGORY: Always Check This Out. Never Sponsored.
8. FOCUS KEYWORD: Short and precise; primary subject term only. After choosing a candidate, score it against the article body; if the score is below 70, switch to a shorter or simpler phrase (for example `bone broth` instead of `bone broth vs stock`).
9. SOCIAL IMAGE: Always mandatory. Never skip.
10. SOCIAL IMAGE SIZE: Exactly 1920×1400 pixels. Target dimensions must use integer floor on both width and height (never round up), so the output is never 1920×1401 or similar off-by-one sizes.
11. SEO TITLE (CULTURAL DAILY): The post title / H1 is never truncated for the article. The SEO plugin field `seo_title` must be only the first 60 characters of the H1 (display cap for SEO tools only).
12. AI LIBERTY POLICY: No creative liberties on H1, body, donation, category, images, keywords. When uncertain — flag for manual review, do not improvise.

13. AUDIT CONFORMITY RULE — The pipeline must never produce output that does not appear in the `our_friends_audit.json` dataset. If a formatting pattern, HTML structure, or content element has never appeared in the 3,208 audited Cultural Daily Our Friends posts, it must not be used. When uncertain about any formatting decision, check `our_friends_audit.json` first. If the pattern is not found there, do not use it.
