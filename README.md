# XenoBase data

Public, versioned archive of processed [XenoBase](https://w3id.org/xenosite/xenobase)
records. Private [xenobase](https://github.com/swamidasslab/xenobase) writes these
files; this repository is the citable snapshot.

The version **is** the git commit. After a UTC calendar day has ended, a manual
GitHub Release tagged `YYYY-MM-DD` may pin that day’s last commit. Zenodo mints a
DOI for each **published GitHub Release**, not for ordinary commits. Cite XenoBase
(and the paper when it exists) **and** the snapshot you used (this commit, or the
Zenodo version DOI once minted).

## Files (settled CURIEs only)

| File | Kind | CURIE |
|---|---|---|
| `molecules.jsonl` | standardized structure | `xbm:NNNNNNN` |
| `reactions.jsonl` | observed reactant → product | `xbr:NNNNNNN` |
| `ids/molecules.jsonl` | mint / retire / revive / relink / merge | same `xbm:` |
| `ids/reactions.jsonl` | mint / retire / revive / relink / merge | same `xbr:` |
| `schema/` | public JSON Schema for those files | |
| `prefixes.json` | compact URI map | |

`id` is the CURIE. `reactant` / `product` are `xbm:` CURIEs. Internal content
hashes, if present, are `content_id`. Rows are sorted by accession. JSON keys are
sorted. There is no version string in the files.

Not yet published (CURIE or public schema not settled): datasets, sources,
networks, pathways, sites, SSSOM mappings. See [TODO.md](TODO.md).

## Cite

Use the GitHub tag or commit, and once Zenodo is connected, the **version DOI**
for that Release. The **concept DOI** (after the first Release) names the dataset
as a whole and resolves to the latest version.

Enable git hooks (canonical form + integrity; rejects the commit, does not rewrite):

```
git config core.hooksPath .githooks
```

```
python3 scripts/check.py
```

Needs `jsonschema` (`pip install jsonschema`).

## Release (manual)

Do not tag during the UTC day. After that day has ended, verify there will be no
more commits, then:

```
python3 scripts/release.py           # checks + prints gh release create
python3 scripts/release.py --publish # tag + GitHub Release (you confirm)
```

A bare `git tag` does not notify Zenodo. The GitHub Release does. Never move a
tag after Zenodo has minted. Tag format is ISO `YYYY-MM-DD`.

Zenodo: log into [sandbox.zenodo.org](https://sandbox.zenodo.org) with GitHub,
toggle this repo On, test one Release. Then the same on [zenodo.org](https://zenodo.org).
Published Zenodo records cannot be deleted. Org owners may need to approve the
Zenodo GitHub app.

## License

[CC BY 4.0](LICENSE). See [NOTICE](NOTICE) for upstream databases (including
ChEMBL CC BY-SA 3.0).
