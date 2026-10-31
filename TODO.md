# Deferred public kinds

Do not add a data file until that kind has a settled xenobase CURIE
(prefix, 7-digit local id, w3id, registry, approved public schema).

- Dataset: imported collections (MetXBioDB, ChEMBL, SwissPharma24, HSDB,
  REFTPS, UACCSCEC), nested SOM datasets, Xenobase Annotations.
  Hierarchical; license and citation on the dataset record.
- Source: paper / figure / table / passage / record under a dataset.
  Optional DOI and ORCID. Same paper is one source (PMID then DOI).
  Use `doi:` as the id when a DOI exists. A source may cite another source.
- Network: `xbn:` is already minted in private xenobase (headline molecule).
  Public `networks.jsonl` waits on an approved public schema. Key is root
  `xbm`, not a mol-content hash.
- Pathway: inferred path for a reaction. Steps reference `xbr`, intermediate
  `xbm` (mint if unobserved), site CURIEs, foreign forest rule ids.
  Provenance on the record; do not publish `data/runs/`.
- Site: site of metabolism on a molecule. Key is `xbm` + stable atom/bond
  locator + site_kind — not SMILES indexes.
- SSSOM: identity, assertion, cite-of-cite, mixture has-part. Predicate
  CURIEs may be minted. Hold files until subject, object, and predicate
  CURIEs are all first-class.
