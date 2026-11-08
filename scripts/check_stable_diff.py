#!/usr/bin/env python3
"""Fail when a git diff of archive downloads looks unstable.

Primary use: ``scripts/release.py`` before squash-merging onto ``release``::

  python scripts/check_stable_diff.py --range release..HEAD

All signals come from ``git diff`` (unified patch + numstat) for that range.

Heuristics:

- volatile / churn field names on added lines
- UUID-looking tokens on added lines
- date stamps on added header lines
- rewrite-like +/− volume (both sides large)
- reorder/whitespace churn (same normalized lines removed and re-added)

Bypass: ``XENOBASE_ALLOW_UNSTABLE_DIFF=1`` (release.py / this script).
"""

from __future__ import annotations

import argparse
import collections
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BYPASS = "XENOBASE_ALLOW_UNSTABLE_DIFF"

WATCH = re.compile(
    r"^(molecules|reactions|networks|prefixes)\.yaml$|"
    r"^ids/.*\.yaml$|"
    r"^mappings/.*\.sssom\.tsv$|"
    r"^schema/.*\.schema\.json$",
)

CHURN_ADDED = re.compile(
    r"(?i)^\+\s*.*\b(generated_at|built_at|mapping_date|mapping_set_version|"
    r"committed_at)\b|"
    r"^\+\s+(commit|version)\s*:\s*\S+"
)
UUID_ADDED = re.compile(
    r"(?i)^\+.*\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
)
DATE_STAMP_ADDED = re.compile(
    r"(?i)^\+\s*#.*(20\d{2}-\d{2}-\d{2})|"
    r"^\+\s*.*\bmapping_date\b.*20\d{2}-\d{2}-\d{2}"
)

REWRITE_MIN = 40
REWRITE_FRAC = 0.35
REORDER_MIN = 20
REORDER_FRAC = 0.5


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True)


def diff_paths(diff_args: list[str]) -> list[str]:
    out = git("diff", *diff_args, "--name-only", "-z")
    return [p for p in out.split("\0") if p and WATCH.search(p)]


def numstat(diff_args: list[str], path: str) -> tuple[int, int]:
    out = git("diff", *diff_args, "--numstat", "--", path).strip()
    if not out:
        return 0, 0
    a, d, *_ = out.split("\t")
    if a == "-" or d == "-":
        return 0, 0
    return int(a), int(d)


def patch_sides(diff_args: list[str], path: str) -> tuple[list[str], list[str], list[str]]:
    patch = git("diff", *diff_args, "--", path)
    added, removed = [], []
    for ln in patch.splitlines():
        if ln.startswith("+++") or ln.startswith("---"):
            continue
        if ln.startswith("+"):
            added.append(ln[1:])
        elif ln.startswith("-"):
            removed.append(ln[1:])
    return patch.splitlines(), added, removed


def _norm(s: str) -> str:
    return s.strip()


def check_path(diff_args: list[str], path: str) -> list[str]:
    problems: list[str] = []
    added_n, removed_n = numstat(diff_args, path)
    patch, added, removed = patch_sides(diff_args, path)
    added_raw = [ln for ln in patch if ln.startswith("+") and not ln.startswith("+++")]

    for ln in added_raw:
        if CHURN_ADDED.search(ln):
            problems.append(f"{path}: volatile field on added line: {ln[1:].strip()[:80]}")
            break
    for ln in added_raw:
        if UUID_ADDED.search(ln):
            problems.append(f"{path}: UUID-looking token on added line")
            break
    for ln in added_raw:
        if DATE_STAMP_ADDED.search(ln):
            problems.append(f"{path}: date/timestamp stamp on added line (prefer git metadata)")
            break

    larger = max(added_n, removed_n)
    if larger >= REWRITE_MIN and added_n and removed_n:
        if min(added_n, removed_n) / larger >= REWRITE_FRAC:
            problems.append(
                f"{path}: rewrite-like diff (+{added_n}/-{removed_n}, "
                f"min/max={min(added_n, removed_n) / larger:.0%})"
            )

    if added and removed and len(added) + len(removed) >= REORDER_MIN:
        a = collections.Counter(_norm(x) for x in added if _norm(x))
        r = collections.Counter(_norm(x) for x in removed if _norm(x))
        overlap = sum((a & r).values())
        denom = min(sum(a.values()), sum(r.values())) or 1
        if overlap / denom >= REORDER_FRAC and overlap >= REORDER_MIN // 2:
            problems.append(
                f"{path}: reorder/whitespace churn "
                f"({overlap} normalized lines both removed and re-added, "
                f"{overlap / denom:.0%} of the smaller side)"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--range", metavar="A..B", help="git diff range (e.g. release..HEAD)")
    g.add_argument("--cached", action="store_true", help="git diff --cached (debug only)")
    args = p.parse_args(argv)

    if os.environ.get(BYPASS) in ("1", "true", "yes"):
        print(f"WARNING: {BYPASS} set — skipping unstable-diff checks", file=sys.stderr)
        return 0

    diff_args = ["--cached"] if args.cached else [args.range]
    paths = diff_paths(diff_args)
    if not paths:
        return 0

    problems: list[str] = []
    for path in paths:
        problems.extend(check_path(diff_args, path))

    if not problems:
        return 0

    seen: set[str] = set()
    uniq = []
    for m in problems:
        if m not in seen:
            seen.add(m)
            uniq.append(m)

    label = "--cached" if args.cached else args.range
    sys.stderr.write(f"xenobase-data: unstable diff (heuristics on git diff {label})\n")
    for msg in uniq:
        sys.stderr.write(f"  ERROR {msg}\n")
    sys.stderr.write(
        "Fix the emitter for a stable patch, or bypass deliberately:\n"
        f"  {BYPASS}=1 python scripts/release.py --publish\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
