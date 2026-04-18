"""
Compare one HTML string (Google Doc body or WordPress-bound body) against
``data/gdoc_intake_profile.json`` and ``data/audit_format_profile.json``.

Used by ``pipeline.run`` for a non-blocking scorecard (warnings only).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from corpus_html_features import analyze_html, metrics_snapshot

REPO_ROOT = Path(__file__).resolve().parent


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
