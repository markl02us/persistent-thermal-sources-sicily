# CHANGELOG

Tracks changes to the Persistent Thermal Sources Catalog — Sicily.

Versioning follows [Semantic Versioning](https://semver.org/): patch for
non-data corrections (authorship, metadata), minor for additive data changes
(new entries, new categories), major for breaking schema changes.

---

## [1.1.0] — 2026-05-29 — First weekly FP harvest (+16 entries)

### Added

- **16 new persistent thermal source entries** auto-promoted from the
  PHOENIX `internal_fires` 7-day window (2026-05-22..2026-05-29) after
  cross-referencing against OSM landuse polygons via Overpass. Breakdown:
  - 10 glasshouse complexes in the Vittoria-Comiso-Pachino belt
  - 3 photovoltaic solar farms in Caltagirone-Catania province
  - 2 urban heat-island patches
  - 1 landfill
- `docs/WEEKLY_FP_REPORT_2026_05_29.md` — full methodology + auto-promotion
  table + manual-review batch description.
- `data/fp_candidates_2026_05_29.json` — raw classified candidate list (50)
  with OSM categories per cell.
- `data/fp_review_batch_2026_05_29.json` — 34 unmatched cells flagged for
  individual human review (could be agricultural burns, undocumented small
  fires, or OSM coverage gaps).
- `data/sources.geojson` regenerated to include the new entries.

### Why minor not patch

This is the first additive data release after the v1.0.1 governance bump.
Per the semver policy in this CHANGELOG, additive data changes bump the
minor; major would only fire on breaking schema changes. New entries
maintain the existing schema exactly.

### Honesty note

The 16 auto-promoted entries are flagged `"annotation": "auto"` in the
JSON. Downstream consumers who want a hand-curated-only subset can filter
on `annotation == "human"` (the original 18 entries are human-confirmed).
The auto-promotions are intended to be high-confidence (clear OSM match)
but consumers should treat them as machine-classified until manual review
is performed.

---

## [1.0.1] — 2026-05-29 — First operational version

### Changed

- **Authorship attribution updated to "Alessandria Della Rocca Applications"**
  in `CITATION.cff`, `.zenodo.json`, `LICENSE.code`, and `docs/methodology.md`.
  The earlier v1.0.0 attribution to "Ludwikowski, M." remains preserved on
  the v1.0.0 Zenodo deposit for citation continuity; v1.0.1 and forward use
  the organizational attribution.
- Gaetano Zambito named as Sicilian representative co-author in CITATION.cff
  and .zenodo.json (he is the project's INGV-facing point of contact).

### Why this version exists

This is the **first operational version** of the catalog — v1.0.0 was the
initial public deposit. v1.0.1 marks the moment we transitioned from
individual-authored research artifact to organizationally-maintained
operational data product, with a public commitment to continuous monitoring,
versioned updates, and a transparent change log.

The data itself (`data/sources.json`, `data/sources.geojson`, etc.) is
unchanged in v1.0.1 — only metadata + governance.

### Added

- `docs/REPO_MIGRATION_PLAN.md` — internal plan for migrating the repo from
  `markl02us/persistent-thermal-sources-sicily` (the original publisher's
  personal namespace) to an Alessandria Della Rocca Applications organization,
  preserving the Zenodo DOI lineage. No timeline; gated on org-creation
  decision.

### Versioning policy going forward

- **Patch (1.0.x)** for metadata/governance updates with no change to data
  entries. Catches typos, fixes attributions, updates CITATION metadata.
- **Minor (1.x.0)** for additive data changes — new persistent-thermal-source
  entries, new category classifications, expanded confidence scores. Existing
  entries' meaning never changes inside a minor release.
- **Major (x.0.0)** for breaking schema changes — renaming fields, removing
  categories, removing entries. Forces re-validation of downstream consumers.

The intent of regular minor releases is to demonstrate to external researchers
and operational consumers that this catalog is actively monitored and
continually improved — not a one-off deposit.

---

## [1.0.0] — 2026-05-24 — Initial Zenodo deposit

- Inaugural public release of the Persistent Thermal Sources Catalog for
  Sicily.
- 18+ entries across volcanic, industrial, glasshouse, quarry, and solar-farm
  categories.
- CC-BY 4.0 (data) + MIT (code).
- Zenodo DOI `10.5281/zenodo.20369891`.
