"""Leading ``SEO Title:`` / ``Meta Description:`` rows stripped from Doc export HTML."""

import doc_parser


def test_strip_seo_title_only_removes_block_and_extracts():
    html = """<!DOCTYPE html><html><head><title>x</title></head><body>
<p>SEO Title: My Custom SEO</p>
<p>First real paragraph.</p>
</body></html>"""
    out, seo, meta, n = doc_parser.strip_leading_gdoc_seo_metadata_from_export_html(html)
    assert n == 1
    assert seo == "My Custom SEO"
    assert meta is None
    assert "SEO Title:" not in out
    assert "First real paragraph." in out


def test_strip_meta_description_only():
    html = """<html><body>
<p class="c1"><span>Meta Description: This is the client meta.</span></p>
<h1>Heading</h1>
</body></html>"""
    out, seo, meta, n = doc_parser.strip_leading_gdoc_seo_metadata_from_export_html(html)
    assert n == 1
    assert seo is None
    assert meta == "This is the client meta."
    assert "Meta Description:" not in out
    assert "Heading" in out


def test_strip_both_in_order():
    html = """<html><body>
<p>seo title: Alpha Title</p>
<p>META DESCRIPTION: Beta meta text.</p>
<p>Article starts here.</p>
</body></html>"""
    out, seo, meta, n = doc_parser.strip_leading_gdoc_seo_metadata_from_export_html(html)
    assert n == 2
    assert seo == "Alpha Title"
    assert meta == "Beta meta text."
    assert "Article starts here." in out
    assert "seo title:" not in out.lower()


def test_stops_on_non_matching_leading_block():
    html = """<html><body>
<p>Editor note: do not remove this line.</p>
<p>SEO Title: Should stay in body</p>
</body></html>"""
    out, seo, meta, n = doc_parser.strip_leading_gdoc_seo_metadata_from_export_html(html)
    assert n == 0
    assert seo is None
    assert meta is None
    assert "SEO Title: Should stay in body" in out


def test_parse_intake_includes_sanitized_html_and_extractions():
    html = """<html><body>
<p>SEO Title: Intake Title</p>
<p>Body only.</p>
</body></html>"""
    intake = doc_parser.parse_google_doc_intake(html, source_url="")
    assert intake["doc_embedded_seo_title"] == "Intake Title"
    assert intake["doc_embedded_meta_description"] is None
    assert intake["leading_seo_metadata_blocks_removed"] == 1
    assert "SEO Title:" not in intake["sanitized_export_html"]
    assert intake["sanitized_export_html"].count("Body only.") == 1
