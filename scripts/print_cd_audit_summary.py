#!/usr/bin/env python3
"""Print human-readable summary from data/audit_format_profile.json (no network)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "data" / "audit_format_profile.json"


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if not path.is_file():
        print(f"Missing {path} — run: python3 scripts/build_audit_format_profile.py", file=sys.stderr)
        sys.exit(1)
    data = json.loads(path.read_text(encoding="utf-8"))
    print(f"Source: {data.get('source', '?')}")
    print(f"Posts: {data.get('posts_fetched', '?')}  author_id={data.get('author_id', '?')}")
    agg = data.get("aggregate") or {}
    if agg:
        print("\nAggregate:")
        for k in sorted(agg.keys()):
            print(f"  {k}: {agg[k]}")
    th = data.get("thresholds") or {}
    if th:
        print("\nThresholds (used by format_to_audit_standard):")
        for k in sorted(th.keys()):
            print(f"  {k}: {th[k]}")
    top20 = data.get("top_20_patterns") or []
    if top20:
        print("\nTop 20 patterns:")
        for row in top20[:20]:
            print(f"  {row.get('rank', '?')}. {row.get('pattern_key', '')} — {row.get('description', '')[:120]}")
    print(f"\nFull file: {path}")


if __name__ == "__main__":
    main()
