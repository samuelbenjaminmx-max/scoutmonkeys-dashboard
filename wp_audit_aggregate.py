"""
Aggregate ``content.rendered`` HTML metrics for Cultural Daily Our Friends audits.

Used by ``scripts/build_audit_format_profile.py`` and ``scripts/audit_sponsored_last_year.py``.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from corpus_html_features import analyze_html


def aggregate_rendered_posts(posts: List[dict]) -> Tuple[Dict[str, Any], int]:
    """
    Sum :func:`corpus_html_features.analyze_html` across WordPress post payloads
    (each must have ``content.rendered``).

    Returns ``(result_dict, posts_with_html_count)``.
    """
    total_tag: Counter = Counter()
    total_h2_sig: Counter = Counter()
    sum_h2 = 0
    sum_h2_num = 0
    sum_ul = sum_ol = sum_li = 0
    sum_gap_s = sum_gap_d = sum_gap_t = 0
    sum_a_p = sum_str_p = 0
    posts_with_html = 0

    for p in posts:
        html = (p.get("content") or {}).get("rendered") or ""
        if not html.strip():
            continue
        posts_with_html += 1
        m = analyze_html(html)
        if not m:
            continue
        sum_h2 += m["h2_total"]
        sum_h2_num += m["h2_numbered"]
        total_h2_sig.update(m["h2_sigs"])
        total_tag.update(m["tag_names"])
        sum_ul += m["ul"]
        sum_ol += m["ol"]
        sum_li += m["li"]
        sum_gap_s += m["gap_single"]
        sum_gap_d += m["gap_double"]
        sum_gap_t += m["gap_triple"]
        sum_a_p += m["anchors_in_p"]
        sum_str_p += m["strong_in_p"]

    top_tags = total_tag.most_common(30)
    top_h2_struct = total_h2_sig.most_common(10)

    numbered_fraction = (sum_h2_num / sum_h2) if sum_h2 else 0.0
    list_total = sum_ul + sum_ol
    ul_fraction = (sum_ul / list_total) if list_total else 1.0

    patterns: List[Dict[str, Any]] = []
    rank = 0
    for tag, cnt in top_tags[:15]:
        rank += 1
        patterns.append(
            {
                "rank": rank,
                "pattern_key": f"tag:{tag}",
                "count": int(cnt),
                "description": f"Element <{tag}> appears {cnt} times across analyzed posts (aggregate).",
            }
        )
    for sig, cnt in top_h2_struct[:5]:
        rank += 1
        patterns.append(
            {
                "rank": rank,
                "pattern_key": f"h2_structure:{sig}",
                "count": int(cnt),
                "description": f"H2 child structure “{sig}” occurs {cnt} times (skeleton, text ignored).",
            }
        )
    patterns.append(
        {
            "rank": rank + 1,
            "pattern_key": "spacing:inter_tag_single_newline",
            "count": int(sum_gap_s),
            "description": f"Serialized `>\\n<` gaps (aggregate): {sum_gap_s}",
        }
    )
    patterns.append(
        {
            "rank": rank + 2,
            "pattern_key": "spacing:inter_tag_double_newline",
            "count": int(sum_gap_d),
            "description": f"Serialized `>\\n\\n<` gaps (aggregate): {sum_gap_d}",
        }
    )
    patterns.append(
        {
            "rank": rank + 3,
            "pattern_key": "links:anchor_inside_p",
            "count": int(sum_a_p),
            "description": f"Anchors nested under <p> (aggregate): {sum_a_p}",
        }
    )
    patterns.append(
        {
            "rank": rank + 4,
            "pattern_key": "inline:strong_inside_p",
            "count": int(sum_str_p),
            "description": f"<strong> inside <p> (aggregate): {sum_str_p}",
        }
    )
    patterns.sort(key=lambda x: -x["count"])
    top_20 = []
    for i, row in enumerate(patterns[:20], start=1):
        row = dict(row)
        row["rank"] = i
        top_20.append(row)

    thresholds = {
        "strip_h2_leading_ordinals": bool(numbered_fraction < 0.5),
        "numbered_h2_fraction_observed": round(numbered_fraction, 5),
        "collapse_runs_of_newlines_ge_3": True,
        "max_serialized_newline_run": 2,
        "lists_ul_share_observed": round(ul_fraction, 5),
    }

    out: Dict[str, Any] = {
        "aggregate": {
            "h2_total": sum_h2,
            "h2_numbered_total": sum_h2_num,
            "numbered_h2_fraction": round(numbered_fraction, 5),
            "ul_total": sum_ul,
            "ol_total": sum_ol,
            "li_total": sum_li,
            "inter_tag_gap_single": sum_gap_s,
            "inter_tag_gap_double": sum_gap_d,
            "inter_tag_gap_triple_plus": sum_gap_t,
        },
        "top_tag_counts": [{"tag": t, "count": int(c)} for t, c in top_tags[:25]],
        "top_h2_structure_signatures": [{"signature": s, "count": int(c)} for s, c in top_h2_struct],
        "top_20_patterns": top_20,
        "thresholds": thresholds,
    }
    return out, posts_with_html
