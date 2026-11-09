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
CURIE = re.compile(r"^(xb[rmns]):([0-9]{7,12})$")
KIND_FILES = {"xbm": "molecules", "xbr": "reactions", "xbn": "networks", "xbs": "sites"}
PREFIX_KIND = {"xbm": "molecule", "xbr": "reaction", "xbn": "network", "xbs": "site"}
# Dataset YAML path → (schema kind, settled CURIE prefix).
PRIMARY_DATASET = {
    "molecules.yaml": ("molecule", "xbm"),
    "reactions.yaml": ("reaction", "xbr"),
    "networks.yaml": ("network", "xbn"),
    "ids/molecules.yaml": ("id_event", "xbm"),
    "ids/reactions.yaml": ("id_event", "xbr"),
    "ids/networks.yaml": ("id_event", "xbn"),
}
SETTLED = ("xbm", "xbr", "xbn")
SOM_SLUGS = ("rainbow", "glutathionation", "glucuronidation", "quinonoid", "epoxide")
ALLOWED_SSSOM = {
    "mappings/reactions.sssom.tsv",
    "mappings/molecules.sssom.tsv",
    "mappings/networks.sssom.tsv",
}
DEFERRED_DATA = {
    "datasets.yaml", "sources.yaml", "pathways.yaml",
    "ids/sources.yaml", "ids/pathways.yaml",
    # Never a public export — xbs: lives on sites/<slug>.yaml rows only.
    "ids/sites.yaml",
    # Legacy JSONL names (retired; reject if reintroduced).
    "datasets.jsonl", "sources.jsonl", "pathways.jsonl", "sites.jsonl",
    "ids/sites.jsonl", "ids/sources.jsonl", "ids/pathways.jsonl",
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


def _graph_nodes(doc: dict) -> list[dict]:
    block = doc.get("jsonld")
    if not isinstance(block, dict):
        raise ValueError("missing jsonld:")
    if "@context" not in block:
        raise ValueError("jsonld missing @context")
    graph = block.get("@graph")
    if not isinstance(graph, list):
        raise ValueError("jsonld missing @graph list")
    if "by_id" in doc or "tables" in doc:
        raise ValueError("legacy tables/by_id not allowed; use jsonld.@graph")
    return [dict(n) for n in graph if isinstance(n, dict)]


def _node_id(obj: dict) -> str | None:
    return obj.get("@id") or obj.get("id")


def _context_prefixes(ctx: dict) -> set[str]:
    out = set()
    for k, v in ctx.items():
        if isinstance(k, str) and not k.startswith("@") and isinstance(v, str) and (
            v.startswith("http://") or v.startswith("https://")
        ):
            out.add(k)
    return out


# Letter-start CURIE only (excludes ISO timestamps like 2026-01-01T00:00:00Z).
_CURIE_PREF = re.compile(r"^([A-Za-z][A-Za-z0-9_.]*):(.+)$")
# Prefixes that must be declared when used (settled + SSSOM source roots + vocab).
_REQUIRED_WHEN_USED = {
    "xb", "xbm", "xbr", "xbn", "xbs", "xbp", "BFO", "skos", "semapv",
    "metxbiodb", "chembl.metabolism", "swisspharma24", "hsdbtps", "reftps", "uaccscec",
}


def _curie_prefix(value: str) -> str | None:
    if not isinstance(value, str) or value.startswith("http"):
        return None
    m = _CURIE_PREF.match(value)
    return m.group(1) if m else None


def _collect_curie_prefixes(obj, out: set[str]) -> None:
    if isinstance(obj, str):
        p = _curie_prefix(obj)
        if p and p in _REQUIRED_WHEN_USED:
            out.add(p)
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and not k.startswith("@"):
                p = _curie_prefix(k)
                if p and p in _REQUIRED_WHEN_USED:
                    out.add(p)
            _collect_curie_prefixes(v, out)
        return
    if isinstance(obj, list):
        for x in obj:
            _collect_curie_prefixes(x, out)


def check_yaml_kind(root: Path, rel: str, kind: str, prefix: str,
                    schemas: dict) -> tuple[list[str], list[dict]]:
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
    if "meta" not in doc:
        problems.append(err(rel, 1, "missing meta:"))
    else:
        meta = doc["meta"] if isinstance(doc["meta"], dict) else {}
        for bad in ("curie_map", "prefixes", "prefix_map", "context"):
            if bad in meta:
                problems.append(
                    err(rel, 1, f"meta.{bad} retired; prefix expansions live in jsonld.@context")
                )
    if any(n.get("iri") for n in (doc.get("jsonld") or {}).get("@graph") or [] if isinstance(n, dict)):
        problems.append(err(rel, 1, "iri field retired; use @id CURIE + @context"))
    try:
        objs = _graph_nodes(doc)
    except ValueError as e:
        return problems + [err(rel, 1, str(e))], []

    ctx = (doc.get("jsonld") or {}).get("@context") or {}
    ctx_prefs = _context_prefixes(ctx) if isinstance(ctx, dict) else set()
    used: set[str] = set()
    _collect_curie_prefixes(objs, used)
    for p in sorted(used - ctx_prefs):
        problems.append(err(rel, 1, f"CURIE prefix {p!r} used in @graph but missing from jsonld.@context"))

    for i, obj in enumerate(objs, 1):
        if CHURN & obj.keys():
            problems.append(err(rel, i, f"churn fields {sorted(CHURN & obj.keys())}"))
        if "iri" in obj:
            problems.append(err(rel, i, "iri field retired; use @id CURIE"))
        if "id" in obj and "@id" not in obj:
            problems.append(err(rel, i, "use @id not id"))
        problems.extend(validate_schema(obj, schemas[kind], rel, i))
        cid = _node_id(obj)
        if isinstance(cid, str):
            m = CURIE.match(cid)
            if m and m.group(1) != prefix:
                problems.append(err(rel, i, f"@id prefix {m.group(1)} != {prefix}"))

    if kind == "id_event":
        keys = [(_node_id(o) or "", o.get("at") or "", o.get("event") or "") for o in objs]
        if keys != sorted(keys):
            problems.append(err(rel, 1, "id-event rows must be sorted by (@id, at, event)"))
        return problems, objs

    ids = [_node_id(o) for o in objs]
    if ids != sorted(ids or []):
        problems.append(err(rel, 1, "nodes must be sorted by @id"))
    if len(ids) != len(set(ids)):
        problems.append(err(rel, 1, "duplicate @id"))
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
    if (root / "prefixes.yaml").exists():
        problems.append(err("prefixes.yaml", 0, "retired; prefixes live in each file's jsonld.@context"))
    if (root / "prefixes.json").exists():
        problems.append(err("prefixes.json", 0, "retired; use jsonld.@context"))

    sites_dir = root / "sites"
    site_files = sorted(sites_dir.glob("*.yaml")) if sites_dir.is_dir() else []
    settled = list(SETTLED)
    if site_files:
        settled.append("xbs")

    required = []
    schema_names = ["id_event"]
    for prefix in settled:
        stem, kind = KIND_FILES.get(prefix), PREFIX_KIND.get(prefix)
        if prefix == "xbs":
            required.extend([f"sites/{s}.yaml" for s in SOM_SLUGS])
            schema_names.append("som_molecule")
            continue
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
    for rel, (_kind, prefix) in PRIMARY_DATASET.items():
        if prefix not in settled_set and (root / rel).exists():
            problems.append(err(rel, 0, "CURIE not settled; do not add this file"))
    for p in site_files:
        slug = p.stem
        if slug not in SOM_SLUGS:
            problems.append(err(f"sites/{p.name}", 0, f"unknown SOM slug {slug!r}"))
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
    som_rows: dict[str, list[dict]] = {}
    if required and set(present) == set(required) and schemas:
        for rel, (kind, prefix) in PRIMARY_DATASET.items():
            if prefix not in settled_set:
                continue
            probs, objs = check_yaml_kind(root, rel, kind, prefix, schemas)
            problems.extend(probs)
            if kind == "id_event":
                events[prefix] = objs
            else:
                records[prefix] = objs
        for slug in SOM_SLUGS:
            rel = f"sites/{slug}.yaml"
            if not (root / rel).exists():
                continue
            probs, objs = check_yaml_kind(root, rel, "som_molecule", "xbm", schemas)
            problems.extend(probs)
            som_rows[slug] = objs

        # xbs: is carried on sites/<slug>.yaml rows (no ids/sites.yaml export).
        seen_ds: dict[str, str] = {}
        for slug, rows in som_rows.items():
            for n, rec in enumerate(rows, 1):
                ds = rec.get("dataset")
                if not ds:
                    problems.append(err(f"sites/{slug}.yaml", n, "missing dataset (xbs:)"))
                    continue
                if not CURIE.match(str(ds)) or not str(ds).startswith("xbs:"):
                    problems.append(err(f"sites/{slug}.yaml", n, f"bad dataset CURIE {ds!r}"))
                    continue
                prev = seen_ds.get(slug)
                if prev and prev != ds:
                    problems.append(
                        err(f"sites/{slug}.yaml", n, f"dataset {ds} disagrees with {prev} in same file")
                    )
                seen_ds[slug] = str(ds)
        if len(set(seen_ds.values())) != len(seen_ds):
            problems.append(err("sites/", 1, "two SOM tables share the same xbs: accession"))

        for prefix, evs in events.items():
            # replay expects id key
            for e in evs:
                if "@id" in e and "id" not in e:
                    e["id"] = e["@id"]
            log = f"{KIND_FILES[prefix]}.yaml"
            state, replay_probs = replay(evs, f"ids/{log}")
            problems.extend(replay_probs)
            by_id = {_node_id(r): r for r in records.get(prefix, [])}
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

        full_mols = {_node_id(r) for r in records.get("xbm", [])}
        for slug, rows in som_rows.items():
            for n, rec in enumerate(rows, 1):
                mid = _node_id(rec)
                if mid and mid not in full_mols:
                    problems.append(
                        err(f"sites/{slug}.yaml", n, f"molecule {mid} not in molecules.yaml")
                    )
        for n, rec in enumerate(records.get("xbr", []), 1):
            if rec.get("status") == "merged":
                continue
            nid = _node_id(rec)
            for side in ("reactant", "product"):
                curie = rec.get(side)
                if rec.get("status") == "active" and not curie:
                    problems.append(err("reactions.yaml", n, f"{side} missing on {nid}"))
                elif curie and curie not in full_mols:
                    problems.append(err("reactions.yaml", n, f"{side} {curie} not in molecules.yaml"))

        rxset = {_node_id(r) for r in records.get("xbr", [])}
        netset = {_node_id(r) for r in records.get("xbn", [])}
        for n, rec in enumerate(records.get("xbn", []), 1):
            if rec.get("status") == "merged":
                continue
            nid = _node_id(rec)
            mol = rec.get("molecule")
            if rec.get("status") == "active" and not mol:
                problems.append(err("networks.yaml", n, f"molecule missing on {nid}"))
            elif mol and mol not in full_mols:
                problems.append(err("networks.yaml", n, f"molecule {mol} not in molecules.yaml"))
            for acc in rec.get("reactions") or []:
                if acc not in rxset:
                    problems.append(err("networks.yaml", n, f"reaction {acc} not in reactions.yaml"))
            for acc in rec.get("molecules") or []:
                if acc not in full_mols:
                    problems.append(err("networks.yaml", n, f"member {acc} not in molecules.yaml"))
            inf = rec.get("inferred")
            if isinstance(inf, dict):
                for acc in inf.get("molecules") or []:
                    if acc not in full_mols:
                        problems.append(err("networks.yaml", n, f"inferred {acc} not in molecules.yaml"))
                for acc in inf.get("replaces") or []:
                    if acc not in rxset:
                        problems.append(err("networks.yaml", n, f"inferred.replaces {acc} not in reactions.yaml"))
                for e in inf.get("edges") or []:
                    if e.get("source") and e["source"] not in full_mols:
                        problems.append(err("networks.yaml", n, f"inferred edge source {e['source']} not in molecules.yaml"))
                    if e.get("target") and e["target"] not in full_mols:
                        problems.append(err("networks.yaml", n, f"inferred edge target {e['target']} not in molecules.yaml"))
                    of = e.get("of")
                    if of and of not in rxset:
                        problems.append(err("networks.yaml", n, f"inferred edge of {of} not in reactions.yaml"))
            elif isinstance(inf, list):
                for acc in inf:
                    if acc not in full_mols:
                        problems.append(err("networks.yaml", n, f"inferred {acc} not in molecules.yaml"))
            for acc in rec.get("contained_in") or []:
                if acc not in netset:
                    problems.append(err("networks.yaml", n, f"contained_in {acc} not in networks.yaml"))
            for e in rec.get("edges") or []:
                eid = e.get("@id") or e.get("id")
                if eid not in rxset:
                    problems.append(err("networks.yaml", n, f"edge {eid} not in reactions.yaml"))
                if e.get("source") and e["source"] not in full_mols:
                    problems.append(err("networks.yaml", n, f"edge source {e['source']} not in molecules.yaml"))
                if e.get("target") and e["target"] not in full_mols:
                    problems.append(err("networks.yaml", n, f"edge target {e['target']} not in molecules.yaml"))

        # Optional claims.yaml (Postgres dump) + ontology.ttl.
        claim_schema_path = root / "schema" / "claim.schema.json"
        claims_path = root / "claims.yaml"
        if claims_path.exists():
            if not claim_schema_path.exists():
                problems.append(err("claims.yaml", 0, "missing schema/claim.schema.json"))
            else:
                problems.extend(canonical_json_file(claim_schema_path))
                claim_schema = load_json(claim_schema_path)
                try:
                    doc = load_yaml(claims_path)
                    graph = ((doc or {}).get("jsonld") or {}).get("@graph") or []
                except Exception as e:  # noqa: BLE001
                    problems.append(err("claims.yaml", 1, f"YAML parse: {e}"))
                    graph = []
                ctx_ids = set()
                catalogue = full_mols | {_node_id(r) for r in records.get("xbr", [])} | {
                    _node_id(r) for r in records.get("xbn", [])
                }
                for n, rec in enumerate(graph, 1):
                    if not isinstance(rec, dict):
                        problems.append(err("claims.yaml", n, "graph node must be an object"))
                        continue
                    problems.extend(validate_schema(rec, claim_schema, "claims.yaml", n))
                    typ = rec.get("@type")
                    nid = rec.get("@id")
                    if typ == "xb:Context" and nid:
                        ctx_ids.add(nid)
                    if typ == "xb:Claim":
                        ctx = rec.get("context")
                        if ctx and ctx not in ctx_ids and not any(
                            g.get("@id") == ctx for g in graph if isinstance(g, dict)
                        ):
                            # Context may appear later in @graph; second pass below.
                            pass
                        for sub in rec.get("subject") or []:
                            if catalogue and sub not in catalogue:
                                problems.append(
                                    err("claims.yaml", n, f"subject {sub} not in settled catalogue")
                                )
                ctx_ids = {
                    g.get("@id") for g in graph
                    if isinstance(g, dict) and g.get("@type") == "xb:Context"
                }
                for n, rec in enumerate(graph, 1):
                    if isinstance(rec, dict) and rec.get("@type") == "xb:Claim":
                        ctx = rec.get("context")
                        if ctx and ctx not in ctx_ids:
                            problems.append(err("claims.yaml", n, f"context {ctx} missing"))

        ont = root / "ontology.ttl"
        if ont.exists():
            text = ont.read_text(encoding="utf-8")
            if "@prefix xb:" not in text or "owl:Ontology" not in text:
                problems.append(err("ontology.ttl", 1, "expected xb: prefix and owl:Ontology"))
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
