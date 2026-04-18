"""
Shared HTML metrics for:
- published Our Friends posts (``scripts/build_audit_format_profile.py``),
- Google Doc exports (``scripts/build_gdoc_intake_profile.py``),
- live pipeline scorecards (``corpus_compare.compare_doc_to_corpora``).
"""
from __future__ import annotations

import re
from typing import Any, Dict

from bs4 import BeautifulSoup, Comment


def _h2_structure_signature(h2) -> str:
    parts: list[str] = ["h2"]
    for child in h2.children:
        if isinstance(child, Comment):
            continue
        if getattr(child, "name", None):
            parts.append(child.name or "?")
        elif str(child).strip():
            parts.append("#text")
    return ">".join(parts)


def analyze_html(html: str) -> Dict[str, Any]:
    """Per-document metrics (no storage of full HTML)."""
    if not (html or "").strip():
        return {}
    raw = html
    soup = BeautifulSoup(html, "html.parser")
    h2_total = 0
    h2_numbered = 0
    h2_sigs: dict[str, int] = {}
    for h2 in soup.find_all("h2"):
        h2_total += 1
        t = h2.get_text(" ", strip=True)
        if t and re.match(r"^\s*\d+\s*[\.\)\-:]\s+\S", t):
            h2_numbered += 1
        sig = _h2_structure_signature(h2)
        h2_sigs[sig] = h2_sigs.get(sig, 0) + 1

    tag_names: dict[str, int] = {}
    for el in soup.find_all(True):
        name = el.name.lower()
        tag_names[name] = tag_names.get(name, 0) + 1

    ul_n = len(soup.find_all("ul"))
    ol_n = len(soup.find_all("ol"))
    li_n = len(soup.find_all("li"))

    gap_single = len(re.findall(r">\s*\n\s*<", raw))
    gap_double = len(re.findall(r">\s*\n\s*\n\s*<", raw))
    gap_triple = len(re.findall(r">\s*\n\s*\n\s*\n\s*<", raw))

    a_in_p = sum(1 for a in soup.find_all("a") if a.find_parent("p") is not None)
    strong_in_p = sum(1 for s in soup.find_all("strong") if s.find_parent("p") is not None)

    return {
        "h2_total": h2_total,
        "h2_numbered": h2_numbered,
        "h2_sigs": h2_sigs,
        "tag_names": tag_names,
        "ul": ul_n,
        "ol": ol_n,
        "li": li_n,
        "gap_single": gap_single,
        "gap_double": gap_double,
        "gap_triple": gap_triple,
        "anchors_in_p": a_in_p,
        "strong_in_p": strong_in_p,
    }


def metrics_snapshot(m: Dict[str, Any]) -> Dict[str, float]:
    """Flatten :func:`analyze_html` output to comparable scalars."""
    if not m:
        return {}
    tags = m.get("tag_names") or {}
    h2t = float(m.get("h2_total") or 0)
    h2n = float(m.get("h2_numbered") or 0)
    return {
        "h2_total": h2t,
        "h2_numbered_fraction": (h2n / h2t) if h2t else 0.0,
        "tag_img": float(tags.get("img", 0)),
        "tag_p": float(tags.get("p", 0)),
        "tag_span": float(tags.get("span", 0)),
        "tag_a": float(tags.get("a", 0)),
        "tag_strong": float(tags.get("strong", 0)),
        "ul": float(m.get("ul") or 0),
        "ol": float(m.get("ol") or 0),
        "li": float(m.get("li") or 0),
        "gap_single": float(m.get("gap_single") or 0),
        "gap_double": float(m.get("gap_double") or 0),
        "anchors_in_p": float(m.get("anchors_in_p") or 0),
        "strong_in_p": float(m.get("strong_in_p") or 0),
    }


def extract_gdoc_body_inner(ghtml: str) -> str:
    """Same as ``pipeline.extract_google_doc_body_inner_html`` (avoid importing pipeline in scripts)."""
    soup = BeautifulSoup(ghtml or "", "html.parser")
    head = soup.find("head")
    if head:
        head.decompose()
    body = soup.body
    if not body:
        return (ghtml or "").strip()
    for junk in list(body.find_all(["script", "style"])):
        junk.decompose()
    parts: list[str] = []
    for child in body.children:
        if isinstance(child, Comment):
            continue
        chunk = str(child).strip()
        if chunk:
            parts.append(chunk)
    return "\n".join(parts).strip() or (ghtml or "").strip()
