"""
Compare one HTML string (Google Doc body or WordPress-bound body) against
``data/gdoc_intake_profile.json`` and ``data/audit_format_profile.json``.

Used by ``pipeline.run`` for a non-blocking scorecard (warnings only) and by
``pipeline.verify_post`` for a blocking corpus QA gate.

Blocking gate
-------------
``score_published_draft(html, audit_profile)`` returns a list of
``CorpusViolation`` objects.  Violations are classified HIGH / MEDIUM / LOW.
``verify_post`` in pipeline.py treats any HIGH violation as a QA failure when
``CD_BLOCK_ON_CORPUS_VIOLATIONS`` is set to ``"1"`` or ``"true"``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from corpus_html_features import analyze_html, metrics_snapshot

REPO_ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Existing intake-comparison helpers (unchanged)
# ---------------------------------------------------------------------------


def _stats_band(
    key: str,
    value: float,
    band: Dict[str, Any],
    *,
    label: str,
) -> Optional[str]:
    """Return a warning string if value is outside min/max with margin, or None."""
    mn = band.get("min")
    mx = band.get("max")
    if mn is None or mx is None:
        return None
    lo = float(mn)
    hi = float(mx)
    if hi <= lo:
        return None
    # Soft margins: allow 15% slack inside the band edges for "typical" docs
    pad = max(1.0, (hi - lo) * 0.15)
    if value < lo - pad:
        return f"{label} {key}={value:g} below intake range [{lo:g}, {hi:g}] (mean {band.get('mean', '?')})"
    if value > hi + pad:
        return f"{label} {key}={value:g} above intake range [{lo:g}, {hi:g}] (mean {band.get('mean', '?')})"
    return None


def compare_doc_to_corpora(
    html: str,
    *,
    gdoc_profile_path: Optional[Path] = None,
    published_profile_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Analyze ``html`` and emit warnings vs saved profiles (if present).

    Returns a dict: ``features``, ``warnings`` (list of str), ``skipped`` (optional),
    ``published_numbered_h2_fraction`` (reference from Our Friends aggregate).
    """
    gdoc_profile_path = gdoc_profile_path or (REPO_ROOT / "data" / "gdoc_intake_profile.json")
    published_profile_path = published_profile_path or (REPO_ROOT / "data" / "audit_format_profile.json")

    raw = analyze_html(html)
    features = metrics_snapshot(raw)
    out: Dict[str, Any] = {"features": features, "warnings": []}
    warnings: List[str] = out["warnings"]

    if published_profile_path.is_file():
        try:
            pub = json.loads(published_profile_path.read_text(encoding="utf-8"))
            agg = pub.get("aggregate") or {}
            h2t = float(agg.get("h2_total") or 0)
            h2n = float(agg.get("h2_numbered_total") or 0)
            frac = (h2n / h2t) if h2t else 0.0
            out["published_numbered_h2_fraction"] = frac
            doc_frac = features.get("h2_numbered_fraction") or 0.0
            # If this doc is almost all numbered H2s but corpus is mostly not, nudge
            if frac < 0.25 and doc_frac > 0.55:
                warnings.append(
                    f"corpus_vs_doc: numbered H2 fraction {doc_frac:.2f} vs published Our Friends ~{frac:.2f} "
                    f"(pipeline may strip leading ordinals via audit profile)"
                )
        except Exception as e:
            out["published_profile_error"] = str(e)
    else:
        out["skipped_published_profile"] = str(published_profile_path)

    if not gdoc_profile_path.is_file():
        out["skipped_gdoc_intake"] = str(gdoc_profile_path)
        return out

    try:
        gd = json.loads(gdoc_profile_path.read_text(encoding="utf-8"))
    except Exception as e:
        out["gdoc_profile_error"] = str(e)
        return out

    bands = gd.get("per_doc_metric_bands") or {}
    for key, band in bands.items():
        if key not in features:
            continue
        msg = _stats_band(key, float(features[key]), band, label="intake")
        if msg:
            warnings.append(msg)

    out["gdoc_docs_ok"] = gd.get("docs_ok")
    out["gdoc_url_file"] = gd.get("url_list_file")
    return out


# ---------------------------------------------------------------------------
# Blocking corpus QA gate — used by pipeline.verify_post
# ---------------------------------------------------------------------------


@dataclass
class CorpusViolation:
    severity: str          # "HIGH" | "MEDIUM" | "LOW"
    rule: str              # short machine key
    message: str           # human-readable explanation
    corpus_value: Any = field(default=None)   # what the corpus says
    draft_value: Any = field(default=None)    # what we found


def load_audit_profile(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or (REPO_ROOT / "data" / "audit_format_profile.json")
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_our_friends_summary(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or (REPO_ROOT / "data" / "our_friends_audit.json")
    if not p.is_file():
        return {}
    try:
        # Only read the summary section — the full file is 3.5 MB
        text = p.read_text(encoding="utf-8", errors="replace")[:32_000]
        blob = json.loads(text)
        return blob.get("summary") or {}
    except Exception:
        return {}


def score_published_draft(
    html: str,
    audit_profile: Optional[Dict[str, Any]] = None,
    our_friends_summary: Optional[Dict[str, Any]] = None,
) -> List[CorpusViolation]:
    """
    Score a *published* WP draft's HTML (``content.raw`` or ``content.rendered``)
    against the Our Friends corpus rules extracted from
    ``data/audit_format_profile.json`` and ``data/our_friends_audit.json``.

    Returns a list of :class:`CorpusViolation` objects ordered HIGH → MEDIUM → LOW.
    An empty list means the draft is clean.

    Rules checked
    -------------
    HIGH (hard publishing contract violations):
      - no_donation_block: missing CLICK HERE TO DONATE footer
      - no_hr_separator: missing <hr /> before donation
      - nextpage_break: <!--nextpage--> present (breaks pagination)
      - double_newline_gaps: serialized HTML has double-blank-line gaps
        (corpus: 0 occurrences across 3208 posts)

    MEDIUM (structural alignment with corpus):
      - numbered_h2_heavy: >50% of H2s are numbered when corpus is <12%
      - h2_bold_wrapper: H2s wrapped in <strong>/<b> when corpus rarely does this
        (corpus signature h2>strong is only 1.2% of H2s)
      - excessive_triple_newlines: any >>>3-newline gap in serialized HTML

    LOW (informational):
      - no_paid_links_bold: body links not wrapped in <strong>
        (corpus: every Our Friends post has paid_style_link_count ≥1)
    """
    if not (html or "").strip():
        return []

    if audit_profile is None:
        audit_profile = load_audit_profile()
    if our_friends_summary is None:
        our_friends_summary = load_our_friends_summary()

    agg = audit_profile.get("aggregate") or {}
    violations: List[CorpusViolation] = []

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    text = html  # raw string for regex checks

    # -----------------------------------------------------------------------
    # HIGH: donation block
    # -----------------------------------------------------------------------
    DONATION_PHRASE = "CLICK HERE TO DONATE"
    has_donation = DONATION_PHRASE in html
    if not has_donation:
        violations.append(CorpusViolation(
            severity="HIGH",
            rule="no_donation_block",
            message=(
                f"Missing donation CTA ({DONATION_PHRASE!r}). "
                "100% of Our Friends posts include this block — it is a publishing contract requirement."
            ),
            corpus_value="present in 100% of posts",
            draft_value="missing",
        ))

    # -----------------------------------------------------------------------
    # HIGH: <hr /> separator
    # -----------------------------------------------------------------------
    has_hr = bool(re.search(r"<hr\s*/?>", html, re.I))
    if not has_hr:
        violations.append(CorpusViolation(
            severity="HIGH",
            rule="no_hr_separator",
            message=(
                "<hr /> separator before donation block is missing. "
                f"Present in {agg.get('hr_total', 2281)}/{audit_profile.get('posts_fetched', 3208)} "
                "Our Friends posts."
            ),
            corpus_value="present in ~71% of posts",
            draft_value="missing",
        ))

    # -----------------------------------------------------------------------
    # HIGH: <!--nextpage--> breaks WP pagination
    # -----------------------------------------------------------------------
    if "<!--nextpage-->" in html:
        violations.append(CorpusViolation(
            severity="HIGH",
            rule="nextpage_break",
            message=(
                "<!--nextpage--> found in draft — this splits the post into multiple WP pages "
                "which breaks the Our Friends layout. Remove it."
            ),
            corpus_value="never present",
            draft_value="found",
        ))

    # -----------------------------------------------------------------------
    # HIGH: double-blank-line gaps in serialized HTML
    # -----------------------------------------------------------------------
    corpus_double = int(agg.get("inter_tag_gap_double") or 0)
    if corpus_double == 0:
        draft_double = len(re.findall(r">\s*\n\s*\n\s*<", text))
        if draft_double > 0:
            violations.append(CorpusViolation(
                severity="HIGH",
                rule="double_newline_gaps",
                message=(
                    f"Serialized HTML has {draft_double} double-blank-line gaps between tags. "
                    "Our Friends corpus has 0 across 3208 posts — these add unwanted whitespace in WP."
                ),
                corpus_value=0,
                draft_value=draft_double,
            ))

    # -----------------------------------------------------------------------
    # MEDIUM: numbered H2 heavy (>50% when corpus is <12%)
    # -----------------------------------------------------------------------
    h2t = int(agg.get("h2_total") or 0)
    h2n = int(agg.get("h2_numbered_total") or 0)
    corpus_numbered_frac = (h2n / h2t) if h2t else 0.0
    if corpus_numbered_frac < 0.25:
        raw_m = analyze_html(html)
        doc_h2t = raw_m.get("h2_total") or 0
        doc_h2n = raw_m.get("h2_numbered") or 0
        doc_frac = (doc_h2n / doc_h2t) if doc_h2t else 0.0
        if doc_h2t >= 2 and doc_frac > 0.50:
            violations.append(CorpusViolation(
                severity="MEDIUM",
                rule="numbered_h2_heavy",
                message=(
                    f"Draft has {doc_h2n}/{doc_h2t} numbered H2s ({doc_frac:.0%}). "
                    f"Our Friends corpus: {corpus_numbered_frac:.0%} numbered. "
                    "The pipeline strips leading ordinals — verify the result looks right."
                ),
                corpus_value=f"{corpus_numbered_frac:.0%}",
                draft_value=f"{doc_frac:.0%}",
            ))

    # -----------------------------------------------------------------------
    # MEDIUM: H2s wrapped in <strong> or <b>
    # -----------------------------------------------------------------------
    h2_sigs = {}
    if audit_profile.get("top_h2_structure_signatures"):
        for row in audit_profile["top_h2_structure_signatures"]:
            h2_sigs[row["signature"]] = row["count"]
    corpus_h2_total = sum(h2_sigs.values()) or 1
    corpus_h2_strong_frac = (h2_sigs.get("h2>strong", 0) + h2_sigs.get("h2>b", 0)) / corpus_h2_total
    draft_h2s = soup.find_all("h2")
    draft_h2_strong = sum(
        1 for h in draft_h2s
        if h.find("strong") or h.find("b")
    )
    draft_h2_strong_frac = (draft_h2_strong / len(draft_h2s)) if draft_h2s else 0.0
    if corpus_h2_strong_frac < 0.05 and draft_h2_strong_frac > 0.30:
        violations.append(CorpusViolation(
            severity="MEDIUM",
            rule="h2_bold_wrapper",
            message=(
                f"Draft has {draft_h2_strong}/{len(draft_h2s)} H2s with bold wrappers ({draft_h2_strong_frac:.0%}). "
                f"Our Friends corpus: only {corpus_h2_strong_frac:.1%}. "
                "Bold H2s are uncommon in the published corpus — check if this is intentional."
            ),
            corpus_value=f"{corpus_h2_strong_frac:.1%}",
            draft_value=f"{draft_h2_strong_frac:.0%}",
        ))

    # -----------------------------------------------------------------------
    # MEDIUM: triple-newline gaps
    # -----------------------------------------------------------------------
    corpus_triple = int(agg.get("inter_tag_gap_triple_plus") or 0)
    if corpus_triple == 0:
        draft_triple = len(re.findall(r">\s*\n(?:\s*\n){2,}\s*<", text))
        if draft_triple > 0:
            violations.append(CorpusViolation(
                severity="MEDIUM",
                rule="excessive_triple_newlines",
                message=(
                    f"Serialized HTML has {draft_triple} triple-or-more newline gap(s). "
                    "Corpus has 0 — these create extra blank lines in WP editor."
                ),
                corpus_value=0,
                draft_value=draft_triple,
            ))

    # -----------------------------------------------------------------------
    # LOW: no bold paid links in body
    # -----------------------------------------------------------------------
    # Pull pre-tail section (before the machine tail marker)
    pre_tail = html
    if "<!--scoutmonkeys-machine-tail-->" in html:
        pre_tail = html.split("<!--scoutmonkeys-machine-tail-->", 1)[0]
    pre_soup = BeautifulSoup(pre_tail, "html.parser")
    body_strong_links = [
        a for a in pre_soup.find_all("a")
        if re.match(r"https?://", a.get("href") or "", re.I)
        and (a.find("strong") or a.find("b") or (
            a.parent and getattr(a.parent, "name", "") in ("strong", "b")
        ))
    ]
    body_http_links = [
        a for a in pre_soup.find_all("a")
        if re.match(r"https?://", a.get("href") or "", re.I)
    ]
    if body_http_links and not body_strong_links:
        violations.append(CorpusViolation(
            severity="LOW",
            rule="no_paid_links_bold",
            message=(
                f"Draft has {len(body_http_links)} body http link(s) but none are wrapped in <strong>. "
                "Our Friends contract: every paid body link must be bold."
            ),
            corpus_value="bold on all paid links",
            draft_value="no bold links found",
        ))

    # Sort HIGH → MEDIUM → LOW
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    violations.sort(key=lambda v: order.get(v.severity, 3))
    return violations


def format_violations(violations: List[CorpusViolation]) -> str:
    """One-line summary per violation for console output."""
    if not violations:
        return "corpus QA: all checks passed."
    lines = []
    for v in violations:
        lines.append(f"  [{v.severity}] {v.rule}: {v.message}")
    return "\n".join(lines)
