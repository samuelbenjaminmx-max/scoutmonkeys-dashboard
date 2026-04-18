#!/usr/bin/env python3
"""
Build ``data/gdoc_intake_profile.json`` from a list of Google Doc URLs (exports).

Aggregates the same HTML metrics as ``corpus_html_features.analyze_html`` / Our Friends
``audit_format_profile``, plus **per-doc min / max / mean** bands for
:func:`corpus_compare.compare_doc_to_corpora`.

Env: none required (public export URLs). Optional: ``GDOCS_MAX_URLS`` to cap fetches.

Usage:
  python3 scripts/build_gdoc_intake_profile.py
  python3 scripts/build_gdoc_intake_profile.py --urls data/training_docs.txt --out data/gdoc_intake_profile.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from corpus_html_features import analyze_html, extract_gdoc_body_inner, metrics_snapshot  # noqa: E402

import doc_parser


def load_env() -> None:
    for p in (ROOT / ".env", Path.cwd() / ".env"):
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, _, v = s.partition("=")
            k, v = k.strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            os.environ[k] = v
        return


def main() -> None:
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", default=str(ROOT / "data" / "training_docs.txt"), help="One Google Doc URL per line")
    ap.add_argument("--out", default=str(ROOT / "data" / "gdoc_intake_profile.json"))
    ap.add_argument("--max", type=int, default=0, help="Max URLs to fetch (0 = all)")
    ap.add_argument("--sleep", type=float, default=0.4, help="Seconds between fetches")
    args = ap.parse_args()

    url_path = Path(args.urls)
    if not url_path.is_file():
        print(f"Missing URL list {url_path}", file=sys.stderr)
        sys.exit(1)

    lines = [ln.strip() for ln in url_path.read_text(encoding="utf-8", errors="replace").splitlines()]
    urls = [ln for ln in lines if ln.startswith("http")]
    cap = (os.environ.get("GDOCS_MAX_URLS") or "").strip()
    if cap.isdigit():
        urls = urls[: int(cap)]
    if args.max and args.max > 0:
        urls = urls[: args.max]

    per_metric: Dict[str, List[float]] = {}
    docs_ok = 0
    errors: List[Dict[str, str]] = []

    for i, url in enumerate(urls, start=1):
        try:
            ghtml = doc_parser.fetch_google_doc_export_by_url(url, attempts=5)
            body = extract_gdoc_body_inner(ghtml)
            m = analyze_html(body)
            snap = metrics_snapshot(m)
            if not snap:
                errors.append({"url": url, "error": "empty metrics"})
                continue
            for k, v in snap.items():
                per_metric.setdefault(k, []).append(float(v))
            docs_ok += 1
            if i % 10 == 0:
                print(f"  …{i}/{len(urls)} ok={docs_ok}", file=sys.stderr)
        except Exception as e:
            errors.append({"url": url, "error": str(e)[:500]})
        time.sleep(max(0.0, args.sleep))

    bands: Dict[str, Dict[str, Any]] = {}
    for k, vals in per_metric.items():
        if not vals:
            continue
        bands[k] = {
            "min": min(vals),
            "max": max(vals),
            "mean": round(statistics.mean(vals), 4),
        }
        if len(vals) >= 2:
            bands[k]["stdev"] = round(statistics.stdev(vals), 4)

    out: Dict[str, Any] = {
        "source": "Google Doc HTML export (public), body inner only — same metrics as audit_format_profile",
        "url_list_file": (
            str(url_path.relative_to(ROOT))
            if str(url_path).startswith(str(ROOT))
            else str(url_path)
        ),
        "urls_attempted": len(urls),
        "docs_ok": docs_ok,
        "docs_errors": len(errors),
        "per_doc_metric_bands": bands,
        "errors_sample": errors[:25],
    }

    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {outp} (docs_ok={docs_ok}, errors={len(errors)})", file=sys.stderr)


if __name__ == "__main__":
    main()
