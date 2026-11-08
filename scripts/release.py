#!/usr/bin/env python3
"""Squash-publish HEAD onto the ``release`` branch.

No GitHub Release assets. Download versions are commits on ``release`` that
touched each file. Before squashing, ``check_stable_diff.py`` runs heuristics
on ``git diff release..HEAD`` (bypass: ``XENOBASE_ALLOW_UNSTABLE_DIFF=1``).

  python scripts/release.py             # dry-run: print plan
  python scripts/release.py --publish   # squash, push release, restore branch

Requires a clean working tree. Commit archive YAML on the working branch first.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRANCH = "release"
BYPASS = "XENOBASE_ALLOW_UNSTABLE_DIFF"


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def run(*args: str) -> None:
    subprocess.check_call(["git", "-C", str(ROOT), *args])


def has_ref(ref: str) -> bool:
    return subprocess.call(
        ["git", "-C", str(ROOT), "rev-parse", "--verify", ref],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) == 0


def current_branch() -> str:
    return git("branch", "--show-current") or "HEAD"


def check_stable(range_spec: str) -> int:
    return subprocess.call(
        [sys.executable, str(ROOT / "scripts" / "check_stable_diff.py"), "--range", range_spec],
        cwd=str(ROOT),
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--publish", action="store_true", help="squash onto release and push")
    args = p.parse_args(argv)

    status = git("status", "--porcelain")
    if status:
        print("working tree is dirty; commit archive changes first", file=sys.stderr)
        return 1

    head = git("rev-parse", "HEAD")
    short = head[:12]
    back = current_branch()
    print(f"HEAD {short} (branch {back or 'detached'})")

    if has_ref(BRANCH):
        range_spec = f"{BRANCH}..HEAD"
        base = git("rev-parse", BRANCH)
        if base == head:
            print(f"{BRANCH} already at HEAD; nothing to squash")
            return 0
        # Unstable-diff gate on what would become the squash payload.
        code = check_stable(range_spec)
        if code != 0:
            print(
                f"unstable diff blocked squash onto {BRANCH}; "
                f"fix emitter or set {BYPASS}=1",
                file=sys.stderr,
            )
            return code
        msg = f"release: squash {short}"
        plan = [
            f"git checkout {BRANCH}",
            f"git merge --squash {head}",
            f"git commit -m {msg!r}",
            f"git push -u origin {BRANCH}",
            f"git checkout {back or head}",
            "python scripts/write_versions_manifest.py  # refresh on release tip (optional follow-up)",
        ]
    else:
        plan = [
            f"git branch {BRANCH} {head}",
            f"git checkout {BRANCH}",
            f"git push -u origin {BRANCH}",
            f"git checkout {back or head}",
        ]
        print(f"(no {BRANCH} yet — first publish is a branch create, not a squash)")

    for line in plan:
        print(line)

    if not args.publish:
        print("re-run with --publish after reviewing")
        return 0

    if has_ref(BRANCH):
        run("checkout", BRANCH)
        run("merge", "--squash", head)
        # Empty squash (no file delta) should not create an empty commit.
        staged = git("diff", "--cached", "--name-only")
        if not staged:
            print("squash produced no staged changes; aborting", file=sys.stderr)
            run("reset", "--hard")
            run("checkout", back or head)
            return 1
        run("commit", "-m", f"release: squash {short}")
        # Refresh versions manifest on the release tip; second commit if it moves.
        subprocess.check_call(
            [sys.executable, str(ROOT / "scripts" / "write_versions_manifest.py")],
            cwd=str(ROOT),
        )
        man = ROOT / ".versions" / "manifest.json"
        if man.exists():
            run("add", str(man.relative_to(ROOT)))
            if git("status", "--porcelain", "--", ".versions/manifest.json"):
                run("commit", "-m", f"release: versions manifest after {short}")
    else:
        run("branch", BRANCH, head)
        run("checkout", BRANCH)

    run("push", "-u", "origin", BRANCH)
    if back:
        run("checkout", back)
    else:
        run("checkout", head)
    print(f"{BRANCH} ← squash of {short}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
