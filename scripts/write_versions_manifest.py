#!/usr/bin/env python3
"""Write/verify ``.versions/manifest.json`` for downloadable archive paths.

The site prefers ``git log release -- path`` at runtime; this manifest is a
stable, reviewable snapshot for scripts and for pre-commit enforcement.

  python scripts/write_versions_manifest.py           # write if needed
  python scripts/write_versions_manifest.py --check   # exit 1 if stale
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRANCH = "release"
OUT = ROOT / ".versions" / "manifest.json"

# Keep in sync with pipeline/publish_data.py ABOUT / site archive.server.ts
SOM_SLUGS = ("rainbow", "glutathionation", "glucuronidation", "quinonoid", "epoxide")
PATHS = [
    "molecules.yaml",
    "reactions.yaml",
    "networks.yaml",
    "ids/molecules.yaml",
    "ids/reactions.yaml",
    "ids/networks.yaml",
    *[f"sites/{s}.yaml" for s in SOM_SLUGS],
    "mappings/reactions.sssom.tsv",
    "mappings/molecules.sssom.tsv",
    "mappings/networks.sssom.tsv",
    "schema/molecule.schema.json",
    "schema/reaction.schema.json",
    "schema/network.schema.json",
    "schema/id_event.schema.json",
    "schema/som_molecule.schema.json",
]


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def release_ref() -> str:
    try:
        git("rev-parse", "--verify", BRANCH)
        return BRANCH
    except subprocess.CalledProcessError:
        return "HEAD"


def versions_for(path: str, tip: str) -> list[dict]:
    if not (ROOT / path).exists() and tip == "HEAD":
        # File may only exist on tip history; still ask git.
        pass
    try:
        out = git("log", tip, "--follow", "--format=%H%x09%cI", "--", path)
    except subprocess.CalledProcessError:
        return []
    rows = []
    for ln in out.splitlines():
        if not ln.strip():
            continue
        commit, at = ln.split("\t", 1)
        rows.append({"commit": commit, "at": at})
    return rows


def build() -> dict:
    tip = release_ref()
    tip_commit = git("rev-parse", tip)
    files = {}
    for path in PATHS:
        vers = versions_for(path, tip)
        files[path] = {
            "versions": vers,
            "latest": vers[0]["commit"] if vers else None,
            "n": len(vers),
        }
    return {
        "branch": tip if tip == BRANCH else None,
        "tip": tip_commit,
        "files": files,
    }


def dumps(obj: dict) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true", help="fail if manifest is missing or stale")
    args = p.parse_args(argv)

    want = dumps(build())
    have = OUT.read_text() if OUT.exists() else None
    if args.check:
        if have != want:
            print(
                f"ERROR {OUT.relative_to(ROOT)}: stale or missing; "
                "run python scripts/write_versions_manifest.py and stage it",
                file=sys.stderr,
            )
            return 1
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    if have == want:
        print(f"{OUT.relative_to(ROOT)} up to date")
        return 0
    OUT.write_text(want)
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
