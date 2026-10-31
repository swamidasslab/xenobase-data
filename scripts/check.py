#!/usr/bin/env python3
"""Canonical form + integrity for xenobase-data. Reject, do not rewrite.

Usage:
  python scripts/check.py [ROOT]
  python scripts/check.py --staged   # check the git index (pre-commit)

Exit 0 if OK. Otherwise print ``path:line: message`` lines and exit 1.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

CHURN = {"version", "built_at", "commit", "mapping_date", "mapping_set_version"}
CURIE = re.compile(r"^(xb[rm]):([0-9]{7})$")
ALLOWED_DATA = {
    "molecules.jsonl": ("molecule", "xbm"),
    "reactions.jsonl": ("reaction", "xbr"),
    "ids/molecules.jsonl": ("id_event", "xbm"),
    "ids/reactions.jsonl": ("id_event", "xbr"),
}
DEFERRED_DATA = {
    "datasets.jsonl", "sources.jsonl", "networks.jsonl", "pathways.jsonl", "sites.jsonl",
    "ids/networks.jsonl", "ids/datasets.jsonl", "ids/sources.jsonl", "ids/pathways.jsonl", "ids/sites.jsonl",
}
SETTLED_FILES = ("molecules.jsonl", "reactions.jsonl", "ids/molecules.jsonl", "ids/reactions.jsonl")
DUMP = dict(sort_keys=True, ensure_ascii=False)


def err(path: str, line: int, msg: str) -> str:
    return f"ERROR {path}:{line}: {msg}"


def load_json(path: Path) -> object:
    return json.loads(path.read_text())


def dumps_line(obj: dict) -> str:
    return json.dumps(obj, **DUMP) + "\n"


def read_jsonl(path: Path) -> list[tuple[int, str, dict | None, str | None]]:
    rows = []
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        return [(0, "", None, f"not UTF-8: {e}")]
    if "\r" in text:
        return [(0, "", None, "CRLF or bare CR; use Unix LF")]
    if text and not text.endswith("\n"):
        rows.append((text.count("\n") + 1, "", None, "missing trailing newline"))
    if text.endswith("\n\n"):
        rows.append((text.count("\n"), "", None, "more than one trailing newline"))
    for n, line in enumerate(text.splitlines(), 1):
        if "\t" in line:
            rows.append((n, line, None, "tab in JSONL"))
            continue
        if line.endswith(" ") or line.endswith("\t"):
            rows.append((n, line, None, "trailing whitespace"))
            continue
        if not line:
            rows.append((n, line, None, "empty line"))
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            rows.append((n, line, None, f"JSON: {e}"))
            continue
        if not isinstance(obj, dict):
            rows.append((n, line, None, "line is not a JSON object"))
            continue
        rows.append((n, line, obj, None))
    return rows


def canonical_file(path: Path) -> list[str]:
    """Byte-identity vs spaced sort_keys dumps, Unix LF, one trailing newline."""
    problems = []
    if path.suffix == ".json":
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            return [err(str(path), 0, f"not UTF-8: {e}")]
        if "\r" in text:
            return [err(str(path), 0, "CRLF or bare CR; use Unix LF")]
        obj = json.loads(text)
        want = json.dumps(obj, indent=2, **DUMP) + "\n"
        if text != want:
            problems.append(err(str(path.as_posix()), 1, "not canonical JSON (indent 2, sort_keys, Unix LF)"))
        return problems
    return problems


def validate_schema(obj: dict, schema: dict, path: str, line: int) -> list[str]:
    try:
        import jsonschema
    except ImportError:
        return [err(path, 0, "jsonschema is not installed (pip install jsonschema)")]
    validator = jsonschema.Draft202012Validator(schema)
    out = []
    for e in validator.iter_errors(obj):
        loc = "/".join(str(x) for x in e.path) or "."
        out.append(err(path, line, f"schema {loc}: {e.message}"))
    return out


def replay(events: list[dict], path: str) -> tuple[dict[str, dict], list[str]]:
    state: dict[str, dict] = {}
    minted: set[str] = set()
    problems = []
    for i, e in enumerate(events, 1):
        acc, ev = e["id"], e["event"]
        if ev == "mint":
            if acc in minted:
                problems.append(err(path, i, f"second mint of {acc}"))
            minted.add(acc)
        elif acc not in minted:
            problems.append(err(path, i, f"{ev} of {acc} before mint"))
        if ev == "merge":
            into = e.get("into")
            if not into:
                problems.append(err(path, i, f"merge {acc} missing into"))
            elif into not in minted:
                problems.append(err(path, i, f"merge {acc} into {into} which has no mint"))
        state[acc] = e
    return state, problems


def status_of(event: str) -> str:
    return {"mint": "active", "revive": "active", "relink": "active",
            "merge": "merged", "retire": "retired"}[event]


def check_jsonl_kind(root: Path, rel: str, kind: str, prefix: str, schemas: dict, prefixes: dict) -> tuple[list[str], list[dict]]:
    path = root / rel
    problems = []
    rows = read_jsonl(path)
    objs: list[tuple[int, dict]] = []
    for n, line, obj, problem in rows:
        if problem:
            problems.append(err(rel, n, problem))
            continue
        assert obj is not None
        if CHURN & obj.keys():
            problems.append(err(rel, n, f"churn fields {sorted(CHURN & obj.keys())}"))
        if dumps_line(obj) != line + "\n":
            problems.append(err(rel, n, "not canonical (sort_keys, spaced separators); rewrite with json.dumps(..., sort_keys=True, ensure_ascii=False)"))
        problems.extend(validate_schema(obj, schemas[kind], rel, n))
        cid = obj.get("id")
        if isinstance(cid, str):
            m = CURIE.match(cid)
            if m and m.group(1) != prefix:
                problems.append(err(rel, n, f"id prefix {m.group(1)} != {prefix}"))
            iri = obj.get("iri")
            base = prefixes["prefixes"].get(prefix)
            if iri and base and m:
                want = base + m.group(2)
                if iri != want:
                    problems.append(err(rel, n, f"iri {iri!r} != {want!r}"))
        objs.append((n, obj))

    if kind == "id_event":
        keys = [(o["id"], o.get("at") or "", o.get("event") or "") for _, o in objs]
        if keys != sorted(keys):
            problems.append(err(rel, 1, "id-event rows must be sorted by (id, at, event)"))
        return problems, [o for _, o in objs]

    ids = [o["id"] for _, o in objs]
    if ids != sorted(ids):
        problems.append(err(rel, 1, "rows must be sorted by id (regno)"))
    if len(ids) != len(set(ids)):
        problems.append(err(rel, 1, "duplicate id"))
    return problems, [o for _, o in objs]


def check_root(root: Path) -> list[str]:
    problems: list[str] = []
    prefixes_path = root / "prefixes.json"
    if not prefixes_path.exists():
        return [err("prefixes.json", 0, "missing")]
    problems.extend(canonical_file(prefixes_path))
    prefixes = load_json(prefixes_path)
    if not isinstance(prefixes, dict) or "prefixes" not in prefixes or "settled" not in prefixes:
        problems.append(err("prefixes.json", 1, "need prefixes and settled"))
        return problems

    schemas = {}
    for name in ("molecule", "reaction", "id_event"):
        p = root / "schema" / f"{name}.schema.json"
        if not p.exists():
            problems.append(err(p.as_posix(), 0, "missing schema"))
            continue
        problems.extend(canonical_file(p))
        schemas[name] = load_json(p)

    present = [rel for rel in SETTLED_FILES if (root / rel).exists()]
    if present and set(present) != set(SETTLED_FILES):
        missing = sorted(set(SETTLED_FILES) - set(present))
        problems.append(err(missing[0], 0, f"settled kinds must be published together; missing {missing}"))

    for rel in DEFERRED_DATA:
        if (root / rel).exists() or list(root.glob("*.sssom.tsv")):
            if rel.endswith(".jsonl") and (root / rel).exists():
                problems.append(err(rel, 0, "CURIE not settled; do not add this file"))
    for sssom in root.glob("**/*.sssom.tsv"):
        problems.append(err(sssom.relative_to(root).as_posix(), 0, "SSSOM held until subject, object, and predicate CURIEs are settled"))

    extra = []
    for p in root.rglob("*.jsonl"):
        rel = p.relative_to(root).as_posix()
        if rel not in ALLOWED_DATA and rel not in DEFERRED_DATA:
            extra.append(rel)
    for rel in extra:
        problems.append(err(rel, 0, "unexpected JSONL; not in the settled prefix map"))

    records: dict[str, list[dict]] = {}
    events: dict[str, list[dict]] = {}
    if set(present) == set(SETTLED_FILES) and schemas:
        for rel, (kind, prefix) in ALLOWED_DATA.items():
            probs, objs = check_jsonl_kind(root, rel, kind, prefix, schemas, prefixes)
            problems.extend(probs)
            if kind == "id_event":
                events[prefix] = objs
            else:
                records[prefix] = objs

        for prefix, evs in events.items():
            state, replay_probs = replay(evs, f"ids/{'molecules' if prefix == 'xbm' else 'reactions'}.jsonl")
            problems.extend(replay_probs)
            by_id = {r["id"]: r for r in records.get(prefix, [])}
            log = f"{'molecules' if prefix == 'xbm' else 'reactions'}.jsonl"
            for acc, last in state.items():
                rec = by_id.get(acc)
                if rec is None:
                    problems.append(err(log, 1, f"{acc} is in the id log but not in {log}"))
                    continue
                want = status_of(last["event"])
                if rec.get("status") != want:
                    problems.append(err(log, 1, f"{acc} status {rec.get('status')!r} != replay {want!r}"))
                if want == "merged" and rec.get("into") != last.get("into"):
                    problems.append(err(log, 1, f"{acc} into {rec.get('into')!r} != replay {last.get('into')!r}"))
            for acc in by_id:
                if acc not in state:
                    problems.append(err(log, 1, f"{acc} has no id-event mint"))

        mols = {r["id"] for r in records.get("xbm", [])}
        for n, rec in enumerate(records.get("xbr", []), 1):
            for side in ("reactant", "product"):
                curie = rec.get(side)
                if rec.get("status") == "merged":
                    continue
                if curie and curie not in mols:
                    problems.append(err("reactions.jsonl", n, f"{side} {curie} not in molecules.jsonl"))
    return problems


def staged_tree() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="xenobase-data-check-"))
    subprocess.run(["git", "checkout-index", "-a", f"--prefix={tmp}/"], check=True)
    return tmp


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", nargs="?", type=Path, default=None)
    p.add_argument("--staged", action="store_true", help="check the git index")
    args = p.parse_args(argv)
    if args.staged:
        root = staged_tree()
    elif args.root:
        root = args.root
    else:
        root = Path(__file__).resolve().parent.parent
    problems = check_root(root)
    if problems:
        sys.stderr.write("\n".join(problems) + "\ncommit aborted\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
