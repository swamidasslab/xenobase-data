# XenoBase data

Public, versioned archive of processed [XenoBase](https://w3id.org/xenosite/xenobase)
records. Private [xenobase](https://github.com/swamidasslab/xenobase) writes these
files; this repository is the citable snapshot.

The version **is** the git commit on the **`release`** branch. Cite XenoBase
(and the paper when it exists) **and** the snapshot you used (this commit).

**JSONL is not an archive format.** `scripts/check.py` rejects `*.jsonl` here.
Public bodies are YAML (`meta` + `jsonld` with `@context` / `@graph`) plus SSSOM
TSV and JSON Schema. Prefix expansions live in each file’s `jsonld.@context`
(every CURIE used in that file, including source roots such as `metxbiodb:`).

## Files (settled CURIEs only)

| File | Access | Kind | CURIE |
|---|---|---|---|
| `molecules.yaml` | login | standardized structure | `xbm:` |
| `reactions.yaml` | login | observed reactant → product | `xbr:` |
| `networks.yaml` | login | forward graph from a headline molecule | `xbn:` |
| `sites/<slug>.yaml` | login | SOM table rows (`rainbow`, …) | `xbm:` + `xbs:` dataset |
| `ids/molecules.yaml` | login | mint / retire / revive / relink / merge | `xbm:` |
| `ids/reactions.yaml` | login | mint / retire / revive / relink / merge | `xbr:` |
| `ids/networks.yaml` | login | mint / retire / revive / relink / merge | `xbn:` |
| `ids/sites.yaml` | login | SOM dataset accession events | `xbs:` |
| `mappings/*.sssom.tsv` | public | SSSOM mappings | |
| `schema/*.schema.json` | public | JSON Schema for graph node shapes | |

Each dataset YAML has outer `meta` (license, software, norms, statistics,
schemas) and `jsonld` (`@context`, `@graph` list of nodes with `@id` CURIEs).
Public SSSOM TSV is free; those predicates are also mirrored onto private
graph nodes. Chemistry lives only in `molecules.yaml`; reactions/networks
reference `xbm:` / `xbr:` CURIEs. SOM downloads are one file per table under
`sites/<slug>.yaml` (`@id` is the molecule; `dataset` is the `xbs:` accession).

Not yet published (CURIE or public schema not settled): sources, pathways,
per-atom site CURIEs. See [TODO.md](TODO.md).

## Cite

Use the `release` commit (and a dated tag when one exists). Cite upstream
databases on the XenoBase Sources page and in [NOTICE](NOTICE).

Enable git hooks:

```
git config core.hooksPath .githooks
```

```
python3 scripts/check.py
```

Needs `jsonschema` (`pip install jsonschema`).

## Release

```
python3 scripts/release.py --publish   # squash HEAD → release after stable-diff gate
```

Bypass unstable-diff: `XENOBASE_ALLOW_UNSTABLE_DIFF=1`. No GitHub Release assets
are required for the download contract (site serves local git).

## License

Unless otherwise noted, this compilation is [CC BY-NC 4.0](LICENSE): free
for academic and other non-commercial use. Commercial licenses are available
from the [Swamidass Lab](https://swami.wustl.edu/). See [NOTICE](NOTICE) for
upstream databases (ChEMBL CC BY-SA 3.0; MetXBioDB, SwissPharma24, HSDBTPS,
REFTPS, UACCSCEC and the LCB merge CC BY 4.0; PubChem and HSDB are US
government works). Those source licenses are not replaced.
