# Deferred public kinds

Do not add a data file until that kind has a settled xenobase CURIE
(prefix, 7-digit local id, w3id, registry, approved public schema).

- Dataset: imported collections (MetXBioDB, ChEMBL, SwissPharma24, HSDB,
  REFTPS, UACCSCEC), Xenobase Annotations. Hierarchical; license and
  citation on the dataset record. (SOM *tables* already ship as
  `sites/<slug>.yaml` with `xbs:` on rows; no `ids/sites.yaml` export.)
- Source: paper / figure / table / passage / record under a dataset.
  Optional DOI and ORCID. Same paper is one source (PMID then DOI).
  Use `doi:` as the id when a DOI exists. A source may cite another source.
- Pathway: inferred path for a reaction. Steps reference `xbr`, intermediate
  `xbm` (mint if unobserved), site CURIEs, foreign forest rule ids.
  Provenance on the record; do not publish `data/runs/`.
- Site CURIE: per-atom/bond site of metabolism on a molecule. Key is
  `xbm` + stable atom/bond locator + site_kind — not SMILES indexes.
  (Table-level `xbs:` + molecule rows in `sites/*.yaml` are settled.)
- SSSOM: identity, assertion, cite-of-cite, mixture has-part. Predicate
  CURIEs may be minted. Hold *new* files until subject, object, and
  predicate CURIEs are all first-class.
