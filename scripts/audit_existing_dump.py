#!/usr/bin/env python3
"""Scan all stored trajectory files for secrets/PII that the old (pre-OPF,
8-rule) sanitizer would have missed.

Reads:
  * trajectories from a directory (default: /var/lib/expool/trajectories)
  * the new 30+ rule client regex set from dist-public/exp_uploader.py
  * optionally the opf package if installed (`--use-opf`)

Writes:
  * a per-experience JSONL audit report with the categories found
  * a summary counting which categories appear in how many experiences

Use this BEFORE shipping the new sanitizer publicly so you know what your
historical pool already contains. If anything HIGH-severity shows up, the
data should be re-sanitized in place (use --rewrite to opt in to that).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


def _load_uploader_module() -> Any:
    """Import dist-public/exp_uploader.py without installing it."""
    here = Path(__file__).resolve().parent.parent
    path = here / "dist-public" / "exp_uploader.py"
    if not path.exists():
        raise SystemExit(f"can't find {path}")
    spec = importlib.util.spec_from_file_location("exp_uploader", path)
    if spec is None or spec.loader is None:
        raise SystemExit("failed to load exp_uploader")
    module = importlib.util.module_from_spec(spec)
    sys.modules["exp_uploader"] = module
    spec.loader.exec_module(module)
    return module


def _load_opf() -> Any | None:
    try:
        from opf._api import RedactorAPI  # type: ignore[import-not-found]
    except Exception as exc:
        print(f"[audit] opf not available ({exc}); skipping OPF pass", file=sys.stderr)
        return None
    ckpt = os.environ.get("OPF_CHECKPOINT", os.path.expanduser("~/.opf/privacy_filter"))
    try:
        return RedactorAPI(checkpoint=ckpt, device=os.environ.get("OPF_DEVICE", "cpu"))
    except Exception as exc:
        print(f"[audit] opf load failed ({exc}); skipping", file=sys.stderr)
        return None


@dataclass
class FileReport:
    path: str
    regex_hits: dict[str, int] = field(default_factory=dict)
    opf_hits: dict[str, int] = field(default_factory=dict)
    high_severity: bool = False
    char_count: int = 0


def _walk_strings(node: Any) -> Iterable[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, list):
        for x in node:
            yield from _walk_strings(x)
    elif isinstance(node, dict):
        for v in node.values():
            yield from _walk_strings(v)


def scan_one(path: Path, uploader: Any, opf_api: Any | None) -> FileReport:
    rep = FileReport(path=str(path))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[audit] skip {path}: {exc}", file=sys.stderr)
        return rep
    high_categories: set[str] = uploader._HIGH_CATEGORIES
    for s in _walk_strings(data):
        if not s:
            continue
        rep.char_count += len(s)
        _, hits = uploader.sanitize(s)
        for k, v in hits.items():
            rep.regex_hits[k] = rep.regex_hits.get(k, 0) + v
            if k in high_categories:
                rep.high_severity = True
        if opf_api is not None:
            try:
                spans = opf_api.redact(s)
            except Exception:
                spans = []
            for span in spans or []:
                label = getattr(span, "label", None) or "unknown"
                rep.opf_hits[label] = rep.opf_hits.get(label, 0) + 1
                if label == "secret":
                    rep.high_severity = True
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default="/var/lib/expool/trajectories",
                    help="directory containing <eid>.json trajectory files")
    ap.add_argument("--out", default="audit_report.jsonl",
                    help="JSONL output path (one record per file)")
    ap.add_argument("--summary", default="audit_summary.json",
                    help="path to write the aggregate summary")
    ap.add_argument("--use-opf", action="store_true",
                    help="also run OPF on every string (slow; needs `opf` installed)")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N files (0 = all)")
    args = ap.parse_args()

    root = Path(args.dir)
    if not root.exists():
        print(f"directory not found: {root}", file=sys.stderr)
        return 2

    uploader = _load_uploader_module()
    opf_api = _load_opf() if args.use_opf else None

    files = sorted(root.glob("*.json"))
    if args.limit:
        files = files[: args.limit]

    out_path = Path(args.out)
    summary_regex: dict[str, int] = {}
    summary_opf: dict[str, int] = {}
    high_count = 0
    total_chars = 0
    files_with_high: list[str] = []

    with out_path.open("w", encoding="utf-8") as fh:
        for i, p in enumerate(files, start=1):
            rep = scan_one(p, uploader, opf_api)
            total_chars += rep.char_count
            for k, v in rep.regex_hits.items():
                summary_regex[k] = summary_regex.get(k, 0) + v
            for k, v in rep.opf_hits.items():
                summary_opf[k] = summary_opf.get(k, 0) + v
            if rep.high_severity:
                high_count += 1
                files_with_high.append(rep.path)
            fh.write(json.dumps({
                "path": rep.path,
                "regex_hits": rep.regex_hits,
                "opf_hits": rep.opf_hits,
                "high_severity": rep.high_severity,
                "char_count": rep.char_count,
            }, ensure_ascii=False) + "\n")
            if i % 100 == 0:
                print(f"[audit] {i}/{len(files)} files scanned", file=sys.stderr)

    summary = {
        "files_scanned": len(files),
        "total_chars": total_chars,
        "files_with_high_severity": high_count,
        "regex_category_totals": dict(sorted(summary_regex.items(), key=lambda x: -x[1])),
        "opf_category_totals": dict(sorted(summary_opf.items(), key=lambda x: -x[1])),
        "high_severity_examples": files_with_high[:20],
    }
    Path(args.summary).write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    print(f"\n=== AUDIT SUMMARY ===")
    print(f"files scanned       : {len(files)}")
    print(f"total chars         : {total_chars:,}")
    print(f"files with HIGH hit : {high_count} ({high_count*100/max(len(files),1):.1f}%)")
    print(f"\nTop regex categories:")
    for k, v in list(summary["regex_category_totals"].items())[:15]:
        print(f"  {k:<24} {v:>6}")
    if summary_opf:
        print(f"\nTop OPF categories:")
        for k, v in list(summary["opf_category_totals"].items())[:15]:
            print(f"  {k:<24} {v:>6}")
    print(f"\nfull report: {out_path}")
    print(f"summary    : {args.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
