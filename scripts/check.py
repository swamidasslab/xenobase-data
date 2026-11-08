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

CHURN = {"version", "built_at", "commit", "mapping_date", "mapping_set_version", "generated_at"}
CURIE = re.compile(r"^(xb[rmn]):([0-9]{7,12})$")
KIND_FILES = {"xbm": "molecules", "xbr": "reactions", "xbn": "networks"}
PREFIX_KIND = {"xbm": "molecule", "xbr": "reaction", "xbn": "network"}
# Primary table name inside each dataset YAML.
PRIMARY_TABLE = {
    "molecules.yaml": ("molecules", "molecule", "xbm"),
    "reactions.yaml": ("reactions", "reaction", "xbr"),
    "networks.yaml": ("networks", "network", "xbn"),
    "ids/molecules.yaml": ("events", "id_event", "xbm"),
    "ids/reactions.yaml": ("events", "id_event", "xbr"),
    "ids/networks.yaml": ("events", "id_event", "xbn"),
}
ALLOWED_SSSOM = {
    "mappings/reactions.sssom.tsv",
    "mappings/molecules.sssom.tsv",
    "mappings/networks.sssom.tsv",
}
DEFERRED_DATA = {
    "datasets.jsonl", "sources.jsonl", "pathways.jsonl", "sites.jsonl",
    "ids/sites.jsonl", "ids/sources.jsonl", "ids/pathways.jsonl",
    "datasets.yaml", "sources.yaml", "pathways.yaml", "sites.yaml",
}


def err(path: str, line: int, msg: str) -> str:
    return f"ERROR {path}:{line}: {msg}"


def load_json(path: Path) -> object:
    return json.loads(path.read_text())


def load_yaml(path: Path):
    try:
        from ruamel.yaml import YAML
    except ImportError:
        # Parent repo may expose ruamel; fall back to PyYAML.
        import yaml
        return yaml.safe_load(path.read_text())
    y = YAML(typ="safe")
    return y.load(path.read_text())


def canonical_json_file(path: Path) -> list[str]:
    problems = []
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        return [err(str(path), 0, f"not UTF-8: {e}")]
    if "\r" in text:
        return [err(str(path), 0, "CRLF or bare CR; use Unix LF")]
    obj = json.loads(text)
    want = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if text != want:
        problems.append(err(str(path.as_posix()), 1, "not canonical JSON (indent 2, sort_keys, Unix LF)"))
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


def _table_rows(doc: dict, table_name: str) -> list[dict]:
    for table in doc.get("tables") or []:
        if not isinstance(table, dict) or table.get("name") != table_name:
            continue
        by_id = table.get("by_id")
        if isinstance(by_id, dict) and by_id:
            rows = []
            for key in sorted(by_id):
                rec = dict(by_id[key] or {})
                rec.setdefault("id", key)
                if rec.get("id") != key:
                    raise ValueError(f"by_id key {key!r} != id {rec.get('id')!r}")
                rows.append(rec)
            return rows
        rows = table.get("rows")
        if isinstance(rows, list):
            return [dict(r) for r in rows]
    return []


def check_yaml_kind(root: Path, rel: str, table_name: str, kind: str, prefix: str,
                    schemas: dict, prefixes: dict) -> tuple[list[str], list[dict]]:
    path = root / rel
    problems: list[str] = []
    if not path.exists():
        return [err(rel, 0, "missing")], []
    text = path.read_text()
    if "\r" in text:
        return [err(rel, 0, "CRLF or bare CR; use Unix LF")], []
    try:
        doc = load_yaml(path)
    except Exception as e:
        return [err(rel, 1, f"YAML: {e}")], []
    if not isinstance(doc, dict):
        return [err(rel, 1, "root must be a mapping")], []
    if "tables" not in doc:
        return [err(rel, 1, "missing tables:")], []
    try:
        objs = _table_rows(doc, table_name)
    except ValueError as e:
        return [err(rel, 1, str(e))], []

    for i, obj in enumerate(objs, 1):
        if CHURN & obj.keys():
            problems.append(err(rel, i, f"churn fields {sorted(CHURN & obj.keys())}"))
        problems.extend(validate_schema(obj, schemas[kind], rel, i))
        cid = obj.get("id")
        if isinstance(cid, str):
            m = CURIE.match(cid)
            if m and m.group(1) != prefix:
                problems.append(err(rel, i, f"id prefix {m.group(1)} != {prefix}"))
            iri = obj.get("iri")
            base = prefixes.get("prefixes", {}).get(prefix)
            if iri and base and m:
                want = base + m.group(2)
                if iri != want:
                    problems.append(err(rel, i, f"iri {iri!r} != {want!r}"))

    if kind == "id_event":
        keys = [(o["id"], o.get("at") or "", o.get("event") or "") for o in objs]
        if keys != sorted(keys):
            problems.append(err(rel, 1, "id-event rows must be sorted by (id, at, event)"))
        return problems, objs

    ids = [o["id"] for o in objs]
    if ids != sorted(ids):
        problems.append(err(rel, 1, "rows must be sorted by id"))
    if len(ids) != len(set(ids)):
        problems.append(err(rel, 1, "duplicate id"))
    return problems, objs


def check_sssom(root: Path, rel: str) -> list[str]:
    path = root / rel
    if not path.exists():
        return []
    problems = []
    text = path.read_text()
    if "\r" in text:
        return [err(rel, 0, "CRLF or bare CR; use Unix LF")]
    cols = None
    rows = []
    for n, ln in enumerate(text.splitlines(), 1):
        if not ln or ln.startswith("#"):
            continue
        cells = ln.split("\t")
        if cols is None:
            cols = cells
            continue
        rows.append((n, cells))
    if cols is None:
        problems.append(err(rel, 1, "missing SSSOM column header"))
        return problems
    data = [c for _, c in rows]
    if data != sorted(data):
        problems.append(err(rel, 1, "SSSOM data rows must be sorted"))
    return problems


def check_root(root: Path) -> list[str]:
    problems: list[str] = []
    prefixes_path = root / "prefixes.yaml"
    legacy = root / "prefixes.json"
    if prefixes_path.exists():
        try:
            prefixes = load_yaml(prefixes_path)
        except Exception as e:
            return [err("prefixes.yaml", 1, str(e))]
    elif legacy.exists():
        problems.extend(canonical_json_file(legacy))
        prefixes = load_json(legacy)
    else:
        return [err("prefixes.yaml", 0, "missing")]
    if not isinstance(prefixes, dict) or "prefixes" not in prefixes or "settled" not in prefixes:
        problems.append(err("prefixes.yaml", 1, "need prefixes and settled"))
        return problems

    settled = list(prefixes.get("settled") or [])
    required = []
    schema_names = ["id_event"]
    for prefix in settled:
        stem, kind = KIND_FILES.get(prefix), PREFIX_KIND.get(prefix)
        if stem:
            required.extend([f"{stem}.yaml", f"ids/{stem}.yaml"])
        if kind:
            schema_names.append(kind)

    schemas = {}
    for name in schema_names:
        p = root / "schema" / f"{name}.schema.json"
        if not p.exists():
            problems.append(err(p.as_posix(), 0, "missing schema"))
            continue
        problems.extend(canonical_json_file(p))
        schemas[name] = load_json(p)

    present = [rel for rel in required if (root / rel).exists()]
    if present and set(present) != set(required):
        missing = sorted(set(required) - set(present))
        problems.append(err(missing[0], 0, f"settled kinds must be published together; missing {missing}"))

    settled_set = set(settled)
    for rel, (_table, _kind, prefix) in PRIMARY_TABLE.items():
        if prefix not in settled_set and (root / rel).exists():
            problems.append(err(rel, 0, "CURIE not settled; do not add this file"))
    for rel in DEFERRED_DATA:
        if (root / rel).exists():
            problems.append(err(rel, 0, "CURIE not settled; do not add this file"))

    for sssom in root.glob("**/*.sssom.tsv"):
        rel = sssom.relative_to(root).as_posix()
        if rel not in ALLOWED_SSSOM:
            problems.append(err(rel, 0, "unexpected SSSOM path"))
        else:
            problems.extend(check_sssom(root, rel))

    for p in root.rglob("*.jsonl"):
        rel = p.relative_to(root).as_posix()
        problems.append(err(rel, 0, "JSONL retired; use YAML archive files"))

    records: dict[str, list[dict]] = {}
    events: dict[str, list[dict]] = {}
    if required and set(present) == set(required) and schemas:
        for rel, (table, kind, prefix) in PRIMARY_TABLE.items():
            if prefix not in settled_set:
                continue
            probs, objs = check_yaml_kind(root, rel, table, kind, prefix, schemas, prefixes)
            problems.extend(probs)
            if kind == "id_event":
                events[prefix] = objs
            else:
                records[prefix] = objs

        for prefix, evs in events.items():
            log = f"{KIND_FILES[prefix]}.yaml"
            state, replay_probs = replay(evs, f"ids/{log}")
            problems.extend(replay_probs)
            by_id = {r["id"]: r for r in records.get(prefix, [])}
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
        # Reactions file also embeds a molecules subset — validate against full molecules.yaml.
        full_mols = {r["id"] for r in records.get("xbm", [])}
        mol_doc_path = root / "molecules.yaml"
        if mol_doc_path.exists():
            try:
                full_mols = {r["id"] for r in _table_rows(load_yaml(mol_doc_path), "molecules")}
            except Exception:
                pass
        for n, rec in enumerate(records.get("xbr", []), 1):
            if rec.get("status") == "merged":
                continue
            for side in ("reactant", "product"):
                curie = rec.get(side)
                if rec.get("status") == "active" and not curie:
                    problems.append(err("reactions.yaml", n, f"{side} missing on {rec.get('id')}"))
                elif curie and curie not in full_mols:
                    problems.append(err("reactions.yaml", n, f"{side} {curie} not in molecules.yaml"))

        rxset = {r["id"] for r in records.get("xbr", [])}
        netset = {r["id"] for r in records.get("xbn", [])}
        for n, rec in enumerate(records.get("xbn", []), 1):
            if rec.get("status") == "merged":
                continue
            mol = rec.get("molecule")
            if rec.get("status") == "active" and not mol:
                problems.append(err("networks.yaml", n, f"molecule missing on {rec.get('id')}"))
            elif mol and mol not in full_mols:
                problems.append(err("networks.yaml", n, f"molecule {mol} not in molecules.yaml"))
            for acc in rec.get("reactions") or []:
                if acc not in rxset:
                    problems.append(err("networks.yaml", n, f"reaction {acc} not in reactions.yaml"))
            for acc in rec.get("molecules") or []:
                if acc not in full_mols:
                    problems.append(err("networks.yaml", n, f"member {acc} not in molecules.yaml"))
            for acc in rec.get("inferred") or []:
                if acc not in full_mols:
                    problems.append(err("networks.yaml", n, f"inferred {acc} not in molecules.yaml"))
            for acc in rec.get("contained_in") or []:
                if acc not in netset:
                    problems.append(err("networks.yaml", n, f"contained_in {acc} not in networks.yaml"))
            for e in rec.get("edges") or []:
                if e.get("id") not in rxset:
                    problems.append(err("networks.yaml", n, f"edge {e.get('id')} not in reactions.yaml"))
                if e.get("source") and e["source"] not in full_mols:
                    problems.append(err("networks.yaml", n, f"edge source {e['source']} not in molecules.yaml"))
                if e.get("target") and e["target"] not in full_mols:
                    problems.append(err("networks.yaml", n, f"edge target {e['target']} not in molecules.yaml"))
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
