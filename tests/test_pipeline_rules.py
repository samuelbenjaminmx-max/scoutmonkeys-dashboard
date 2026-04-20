"""
Mandatory pipeline rule tests — all must pass before any commit is pushed.

Run manually:  python -m pytest tests/test_pipeline_rules.py -v
Auto-enforced: .git/hooks/pre-commit
"""
import re
import sys
sys.path.insert(0, '.')

import pytest
from unittest.mock import patch
from bs4 import BeautifulSoup

from pipeline import (
    ensure_meta_description_length,
    extract_h1_from_gdoc_html,
    cd_title_focus_keyword_candidates,
    refine_focus_keyword_for_content,
    cd_guaranteed_hero_strip,
    cd_extract_image_source_credits,
)


# ─────────────────────────────────────────────────────────────────────────────
# Rule 1 — Meta description: ≤ 160 chars total, always ends with a period
# ─────────────────────────────────────────────────────────────────────────────

class TestMetaDescriptionLength:
    @pytest.mark.parametrize("meta,filler", [
        ("A" * 200, ""),                              # way over limit
        ("A" * 161, ""),                              # one over
        ("A" * 160, ""),                              # at limit, no period yet
        ("A" * 159 + ".", ""),                        # exactly 160 with period
        ("Short.", "B" * 200),                        # short meta, long filler
        ("", "Body content fills this in. " * 15),   # empty meta, derive from filler
        ("", ""),                                      # both empty — allowed to return ""
        ("Word " * 30 + "end.", ""),                  # long multi-word, clips at boundary
    ])
    def test_never_exceeds_160_chars(self, meta, filler):
        result = ensure_meta_description_length(meta, filler)
        assert len(result) <= 160, (
            f"Meta is {len(result)} chars (max 160): {result!r}"
        )

    @pytest.mark.parametrize("meta,filler", [
        ("This is a normal sentence", "More padding text to reach minimum length here."),
        ("A" * 200, "padding"),
        ("", "Some body text used as filler for the meta description test."),
        ("Short text here", "Extra filler content to bring it above 120 characters minimum length needed."),
    ])
    def test_always_ends_with_period(self, meta, filler):
        result = ensure_meta_description_length(meta, filler)
        if result:  # empty string is allowed only when both inputs are empty
            assert result.endswith("."), (
                f"Meta does not end with period: {result!r}"
            )

    def test_period_plus_text_never_exceeds_160(self):
        # Regression: clip to 159, add period = 160 exactly
        meta = "X" * 159  # 159 chars, no period
        result = ensure_meta_description_length(meta, "")
        if result:
            assert len(result) <= 160
            assert result.endswith(".")


# ─────────────────────────────────────────────────────────────────────────────
# Rule 2 — Post title is the H1 verbatim — never shortened or altered
# ─────────────────────────────────────────────────────────────────────────────

class TestPostTitleIsH1Verbatim:
    def test_explicit_h1_tag_returned_verbatim(self):
        html = "<html><body><h1>Your Complete Guide to Visiting Gatlinburg</h1><p>body</p></body></html>"
        assert extract_h1_from_gdoc_html(html) == "Your Complete Guide to Visiting Gatlinburg"

    def test_long_title_not_truncated(self):
        long_title = "Why Regular AC Tune-Ups Improve Your Home's Indoor Air Quality and Comfort Level"
        html = f"<html><body><h1>{long_title}</h1><p>body</p></body></html>"
        assert extract_h1_from_gdoc_html(html) == long_title

    def test_title_css_class_paragraph_returned_verbatim(self):
        html = '<html><body><p class="title">Sponsored Article Title Here</p><p>body</p></body></html>'
        assert extract_h1_from_gdoc_html(html) == "Sponsored Article Title Here"

    def test_bold_only_paragraph_detected_as_h1(self):
        html = (
            "<html><body>"
            "<p><strong>All Bold Paragraph Title Text Here</strong></p>"
            "<p>Regular body sentence follows here.</p>"
            "</body></html>"
        )
        result = extract_h1_from_gdoc_html(html)
        assert result == "All Bold Paragraph Title Text Here"

    def test_special_characters_preserved(self):
        title = "5 Reasons Why It's Great: A Guide & More"
        html = f"<html><body><h1>{title}</h1><p>body</p></body></html>"
        assert extract_h1_from_gdoc_html(html) == title


# ─────────────────────────────────────────────────────────────────────────────
# Rule 3 — Focus keyword is always a word or phrase from the H1 title
# ─────────────────────────────────────────────────────────────────────────────

class TestFocusKeywordFromTitle:
    TITLE = "Best Casinos in Las Vegas for High Rollers"
    BODY = "<p>Las Vegas casinos offer high rollers exclusive perks and rewards.</p>" * 4

    def _title_words(self):
        return set(re.findall(r"[a-z0-9]+", self.TITLE.lower()))

    def _run(self, mock_response):
        with patch("pipeline._anthropic_messages", return_value=mock_response):
            return refine_focus_keyword_for_content(
                "", body=self.BODY, doc_html="", title=self.TITLE, topic_slug="test"
            )

    def _assert_from_title(self, kw):
        kw_words = set(re.findall(r"[a-z0-9]+", kw.lower()))
        assert kw_words, f"Focus keyword is empty: {kw!r}"
        assert kw_words.issubset(self._title_words()), (
            f"Keyword {kw!r} contains words not from title {self.TITLE!r}. "
            f"Unexpected words: {kw_words - self._title_words()}"
        )

    def test_keyword_from_title_when_claude_correct(self):
        self._assert_from_title(self._run("casinos"))

    def test_keyword_from_title_when_claude_returns_garbage(self):
        self._assert_from_title(self._run("completely_random_xyz_not_in_title"))

    def test_keyword_from_title_when_claude_off_script(self):
        # Simulates Claude explaining its reasoning instead of returning one word
        self._assert_from_title(self._run(
            "ac tune-ups\n\nWait, that's not in the list. Let me re-read.\n\nair quality"
        ))

    def test_keyword_from_title_when_claude_api_fails(self):
        with patch("pipeline._anthropic_messages", side_effect=Exception("API down")):
            kw = refine_focus_keyword_for_content(
                "", body=self.BODY, doc_html="", title=self.TITLE, topic_slug="test"
            )
        self._assert_from_title(kw)

    def test_keyword_never_empty(self):
        kw = self._run("casinos")
        assert kw.strip(), "Focus keyword must not be empty"

    def test_gatlinburg_beats_trip_and_pre(self):
        """Regression: 'pre' and 'trip' must not beat the specific toponym 'gatlinburg'."""
        title = "Your Pre-Trip Checklist for Visiting Gatlinburg With Your Dog"
        body = ("<p>Gatlinburg is a popular destination for dog owners. "
                "Visiting Gatlinburg with your dog is a rewarding trip. "
                "Gatlinburg offers many pet-friendly trails.</p>") * 3
        with patch("pipeline._anthropic_messages", return_value="gatlinburg"):
            kw = refine_focus_keyword_for_content(
                "", body=body, doc_html="", title=title, topic_slug="gatlinburg"
            )
        assert kw == "gatlinburg", f"Expected 'gatlinburg', got {kw!r}"

    def test_prefix_words_never_selected_as_fallback(self):
        """Stopword prefixes like 'pre', 'non', 're' must never be focus keyword."""
        title = "Pre-Season Non-Negotiable Steps for Re-Training Your Dog"
        body = "Pre-season training for dogs. Non-negotiable steps for re-training."
        candidates = cd_title_focus_keyword_candidates(title)
        for c in candidates:
            assert c not in ("pre", "non", "re", "de", "pro", "anti", "co", "vs", "via", "per"), (
                f"Prefix {c!r} leaked into candidates: {candidates}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Rule 4 — Hero image never appears in the post body
# ─────────────────────────────────────────────────────────────────────────────

class TestHeroNotInBody:
    def test_hero_src_stripped_from_body(self):
        hero = "https://cdn.example.com/hero-article-image.jpg"
        body = f"<p>Opening paragraph.</p><img src=\"{hero}\" alt=\"hero\" /><p>Second paragraph.</p>"
        result = cd_guaranteed_hero_strip(body, hero)
        assert hero not in result, f"Hero src still found in body after strip"

    def test_no_img_tag_with_hero_src_after_strip(self):
        hero = "https://example.com/hero.jpg"
        body = f'<figure><img src="{hero}" /></figure><p>Body text.</p>'
        result = cd_guaranteed_hero_strip(body, hero)
        soup = BeautifulSoup(result, "html.parser")
        for img in soup.find_all("img"):
            assert img.get("src") != hero, "Hero <img> still present after strip"

    def test_non_hero_images_are_preserved(self):
        hero = "https://example.com/hero.jpg"
        body_img = "https://example.com/body-image.jpg"
        body = f'<img src="{hero}" /><img src="{body_img}" />'
        result = cd_guaranteed_hero_strip(body, hero)
        soup = BeautifulSoup(result, "html.parser")
        srcs = [img.get("src") for img in soup.find_all("img")]
        assert body_img in srcs, "Non-hero image was incorrectly removed"
        assert hero not in srcs, "Hero image was not removed"

    def test_empty_body_returns_safely(self):
        assert cd_guaranteed_hero_strip("", "https://example.com/hero.jpg") == ""

    def test_empty_hero_src_returns_body_unchanged(self):
        body = "<p>Body unchanged when hero src is empty.</p>"
        assert cd_guaranteed_hero_strip(body, "") == body


# ─────────────────────────────────────────────────────────────────────────────
# Rule 5 — Image-source links → nofollow credit paragraph, correct format
# ─────────────────────────────────────────────────────────────────────────────

class TestImageSourceCredits:
    @pytest.mark.parametrize("link_text", [
        "Image source", "image source", "IMAGE SOURCE",
        "Source", "source",
        "Photo source", "PHOTO SOURCE",
    ])
    def test_matching_anchor_removed_from_body(self, link_text):
        body = f'<p>Text.</p><p><a href="https://unsplash.com/photos/abc">{link_text}</a></p><p>More.</p>'
        new_body, _ = cd_extract_image_source_credits(body)
        soup = BeautifulSoup(new_body, "html.parser")
        anchors = [a.get_text(strip=True).lower() for a in soup.find_all("a")]
        assert link_text.lower() not in anchors, (
            f"Image-source anchor still in body: {new_body!r}"
        )

    def test_credit_contains_nofollow(self):
        body = '<p><a href="https://unsplash.com/photos/abc">Image source</a></p>'
        _, credits = cd_extract_image_source_credits(body)
        assert "nofollow" in credits

    def test_credit_format_photo_via_domain(self):
        body = '<p><a href="https://unsplash.com/photos/abc">Source</a></p>'
        _, credits = cd_extract_image_source_credits(body)
        assert "Photo: via" in credits
        assert "unsplash.com" in credits

    def test_credit_is_italic_em_paragraph(self):
        body = '<p><a href="https://pexels.com/photo/123">Photo source</a></p>'
        _, credits = cd_extract_image_source_credits(body)
        assert credits.startswith("<p><em>")
        assert credits.endswith("</em></p>")

    def test_credit_has_target_blank(self):
        body = '<p><a href="https://example.com/img.jpg">Image source</a></p>'
        _, credits = cd_extract_image_source_credits(body)
        assert 'target="_blank"' in credits

    def test_no_credits_for_regular_links(self):
        body = '<p>Normal paragraph with <a href="https://example.com">a regular link</a>.</p>'
        new_body, credits = cd_extract_image_source_credits(body)
        assert credits == "", f"Unexpected credit for regular link: {credits!r}"

    def test_multiple_image_source_links_all_extracted(self):
        body = (
            '<p><a href="https://unsplash.com/1">Image source</a></p>'
            '<p>Middle paragraph.</p>'
            '<p><a href="https://pexels.com/2">Photo source</a></p>'
        )
        new_body, credits = cd_extract_image_source_credits(body)
        assert credits.count("<p><em>") == 2, "Expected 2 credit paragraphs"
        assert "unsplash.com" in credits
        assert "pexels.com" in credits
