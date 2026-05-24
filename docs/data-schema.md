# Data schema — `data/sources.json`

Top-level: array of source objects.

Each source has:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Stable identifier (e.g. `fp_etna_summit`) |
| `lat` | float | Latitude WGS84 |
| `lon` | float | Longitude WGS84 |
| `radius_km` | float | Exclusion radius in km |
| `category` | string | One of: `volcanic`, `industrial`, `glasshouse_complex`, `quarry`, `solar_farm`, `urban` |
| `subcategory` | string (optional) | Finer-grained type (e.g. `refinery`, `vegetable_greenhouse`) |
| `name_it` | string | Italian name |
| `name_en` | string | English name |
| `osm_tags` | object (optional) | Matching OpenStreetMap tags |
| `vision_result` | object (optional) | AI-vision classification record |
| `google_maps_url` | string | Direct Google Maps link |
| `first_seen` | ISO-8601 timestamp | When the source was first flagged |
| `last_verified` | ISO-8601 timestamp | When the source was last reviewed |

`data/sources.geojson` is the same data as a GeoJSON FeatureCollection.
`data/sources.csv` is a flat tabular export.
