#!/usr/bin/env python3
"""Manual UTC-day GitHub Release. Does not mint on its own.

After the UTC calendar day of the latest commit has ended, and that commit
differs from the previous YYYY-MM-DD tag, this prints (or with --publish runs)
``gh release create YYYY-MM-DD`` on that commit. Zenodo sees the published
Release, not the tag alone.

  python scripts/release.py           # check + print the command
  python scripts/release.py --publish # create the tag and GitHub Release
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

TAG = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ROOT = Path(__file__).resolve().parent.parent


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--publish", action="store_true", help="create the tag and GitHub Release (after checks)")
    args = p.parse_args(argv)

    status = git("status", "--porcelain")
    if status:
        print("working tree is dirty; commit or stash first", file=sys.stderr)
        return 1

    commit = git("rev-parse", "HEAD")
    commit_unix = int(git("log", "-1", "--format=%ct", "HEAD"))
    day = dt.datetime.fromtimestamp(commit_unix, tz=dt.UTC).date()
    tag = day.isoformat()
    today = dt.datetime.now(dt.UTC).date()
    if day >= today:
        print(f"UTC day {tag} has not ended (today is {today.isoformat()}); no tag yet", file=sys.stderr)
        return 1

    existing = [t for t in git("tag", "-l").splitlines() if TAG.match(t)]
    if tag in existing:
        print(f"tag {tag} already exists; never retag after Zenodo mints", file=sys.stderr)
        return 1

    last = max(existing) if existing else None
    if last:
        changed = git("diff", "--stat", last, "HEAD")
        if not changed:
            print(f"no file changes since {last}; no release", file=sys.stderr)
            return 1

    cmd = ["gh", "release", "create", tag, "--repo", "swamidasslab/xenobase-data",
           "--title", f"XenoBase data {tag}", "--notes",
           f"UTC snapshot {tag} at {commit}. Cite this GitHub Release (and its Zenodo version DOI once minted)."]
    print(" ".join(cmd))
    if not args.publish:
        print("re-run with --publish after you have verified there will be no more commits for this UTC day")
        return 0
    subprocess.check_call(["git", "-C", str(ROOT), "tag", "-a", tag, "-m", f"XenoBase data {tag}"])
    subprocess.check_call(["git", "-C", str(ROOT), "push", "origin", tag])
    subprocess.check_call(cmd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
