# Persistent Thermal Sources - Sicily

A public, open-data catalog of **persistent thermal anomalies in Sicily** that
commonly trigger false-positive wildfire detections from satellite thermal
sensors (NASA FIRMS-VIIRS/MODIS, EUMETSAT MTG-FCI, Sentinel-3 SLSTR, etc.).

This catalog is produced by [PHOENIX](https://adr-wildfire.com/), an
academic non-commercial wildfire-detection research system. It is intended
to help anyone building wildfire detection or analytics pipelines to
filter out chronic non-fire heat sources.

## Sources catalogued

18+ sources across 5 categories:

| Category               | Count | Example                                |
|------------------------|-------|----------------------------------------|
| Volcanic               | 3     | Etna summit, Stromboli, Vulcano        |
| Industrial / refinery  | 5     | Augusta-Priolo, Gela, Milazzo          |
| Glasshouse complex     | 5     | Pachino tomato belt, Vittoria-Comiso   |
| Quarry                 | 3     | Sciacca, Caltanissetta                 |
| Solar farm             | 1     | (rural Sicily)                         |
| Urban heat island      | 1     | Palermo center                         |

See `data/sources.json` for the full machine-readable list with:
- coordinates (WGS84)
- exclusion radius (km)
- category + subcategory
- OSM tags where applicable
- Google Maps + Street View URLs
- AI-vision classification confidence (Claude Sonnet 4.5)
- timestamp of last validation

## Why this catalog exists

Satellite thermal sensors see all hot pixels, not just fires. Refineries,
volcanoes, large greenhouse complexes, and (occasionally) solar farms
look like fires from space. Every operational fire-detection service
filters these out internally, but their masks are not public. **This is.**

Use it to: build cleaner training data, filter your own detector's
output, build similar catalogs for other regions.

## How the catalog was built

1. **Mining** (`scripts/miner.py`): we looked at our own and FIRMS' 30-day
   detection history and flagged cells with >=6 hits across >=3 days that
   were never confirmed by Sentinel-2 burn-scar.
2. **Vision classification** (`scripts/pipeline.py`): for each candidate
   we downloaded an Esri satellite tile and asked Claude Sonnet 4.5
   what's there. Confidence >=0.85 = auto-annotate.
3. **OSM tag lookup**: cross-reference with OpenStreetMap to confirm
   land-use (refinery, glasshouse, etc.).
4. **Human review** (low-confidence batches only): items <0.85 confidence
   are batched daily for manual review.

## License

- **Data** (`data/`, `evidence/`): [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- **Code** (`scripts/`): [MIT](LICENSE.code)

## Citation

```bibtex
@dataset{phoenix_fp_catalog_sicily_2026,
  author       = {Ludwikowski, Mark},
  title        = {Persistent Thermal Sources Catalog - Sicily},
  year         = 2026,
  publisher    = {Zenodo},
  version      = {v1.0.0},
  doi          = {10.5281/zenodo.20369891}
}
```

## Contributing

Spotted a source we missed? Open an issue with lat/lon + a photo or
satellite-tile URL. Spotted a false catalog entry (e.g., a real fire
miscategorized)? Same - open an issue.

## Related

- Live PHOENIX map: [adr-wildfire.com](https://adr-wildfire.com/)
- Methodology: [adr-wildfire.com/come-funziona](https://adr-wildfire.com/come-funziona)
- Live feed accuracy: [adr-wildfire.com/accuracy.html](https://adr-wildfire.com/accuracy.html)

---

*This is a research artifact from an academic, non-commercial wildfire
detection system. Not affiliated with NASA, EUMETSAT, Copernicus, or any
operational fire-management agency.*
