# Geospatial File Measurement API

FastAPI service that accepts a **zipped Shapefile** or a **KML**, extracts every feature (index, geometry type, geometry, CRS, properties) and returns **area / length measurements computed in metres** after projecting to a suitable UTM zone.

**Stack:** Python 3.11+, FastAPI, SQLAlchemy (SQLite by default), pyshp, lxml, Shapely 2, pyproj, pytest. No GDAL install required (all pure wheels), so setup is just `pip install`.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:create_app --factory --reload          # Swagger UI: http://127.0.0.1:8000/docs
pytest -q                                               # run the tests
```
Optional env vars: `DATABASE_URL` (default `sqlite:///./geoapi.db`), `MAX_UPLOAD_MB` (50), `MAX_UNZIPPED_MB` (500).

## API

### `POST /api/files/` – upload & process
Multipart form: `file` (`.zip` containing a Shapefile, or `.kml`); optional `assume_crs` (e.g. `EPSG:4326`) for shapefiles lacking a `.prj`.
```bash
curl -F "file=@survey.kml" http://127.0.0.1:8000/api/files/
```
`201`:
```json
{"id": "9f2c…", "filename": "survey.kml", "file_type": "kml", "feature_count": 3, "crs": "EPSG:4326",
 "status": "COMPLETED", "error": null, "geometry_types": {"Polygon": 1, "LineString": 1, "Point": 1}, "created_at": "…"}
```
Errors: `400` wrong extension / bad `assume_crs`; `413` too large; `422` unreadable file (corrupt ZIP, no `.shp`, missing `.prj`, malformed KML) – the body is `{"detail": "...", "id": "...", "status": "FAILED"}` and the failed record stays queryable.

### `GET /api/files/{id}/` – file information
Same shape as the upload response (`id, filename, feature_count, crs, status`, + `geometry_types`, `error`).

### `GET /api/files/{id}/measurements/?limit=1000&offset=0`
```json
{
  "file_id": "9f2c…", "source_crs": "EPSG:4326", "total": 3, "limit": 1000, "offset": 0,
  "totals": {"area_sq_m": 1201683.9, "area_hectares": 120.17, "length_m": 1086.4},
  "measurements": [
    {"feature_id": 0, "geometry_type": "Polygon", "measurement_status": "OK", "measurement_crs": "EPSG:32643",
     "measurements": {"area_sq_m": 1201683.9191, "area_hectares": 120.168392, "perimeter_m": 4385.0619}, "message": null},
    {"feature_id": 1, "geometry_type": "LineString", "measurement_status": "OK", "measurement_crs": "EPSG:32643",
     "measurements": {"length_m": 1086.4}, "message": null},
    {"feature_id": 2, "geometry_type": "Point", "measurement_status": "NOT_APPLICABLE", "measurement_crs": null,
     "measurements": {}, "message": "points have no area or length"}
  ]
}
```
`measurement_status`: `OK` · `NOT_APPLICABLE` (points) · `UNSUPPORTED` (MultiPoint, GeometryCollection, empty/unparsable geometry) · `ERROR` (e.g. coordinates impossible for the declared CRS). A single bad feature never fails the file. `totals` cover the whole file, not just the page.

### `GET /api/files/{id}/features/?limit=&offset=` (extra)
Every feature: `{"id": 0, "geometry_type": "Polygon", "geometry": {GeoJSON, in the file's CRS}, "crs": "EPSG:4326", "properties": {…}}`.

`GET /health` for liveness. `409` is returned by `/features/` and `/measurements/` for a `FAILED` file; `404` for unknown ids.

## Architecture

```
app/
  main.py            app factory (settings + DB injected → easy to test)
  routers/files.py   HTTP layer: upload streaming, size/type checks, pagination
  services/readers.py   Shapefile (pyshp) and KML (lxml) → shapely geometries + properties
  services/crs.py       CRS labelling, UTM zone selection, cached transformers
  services/measure.py   measure one geometry in metres
  services/ingest.py    glue: read → measure each feature → persist
  models.py / schemas.py   SQLAlchemy tables (files, features) / API models
```

**File-processing flow.** Upload is streamed to a temp file (size-capped) → type detected from extension → reader parses it → each feature becomes a `Feature` row (GeoJSON geometry in the original CRS, properties, measurement result) → file marked `COMPLETED` (or `FAILED` with the reason). Processing is synchronous inside the request; measurements are computed once at ingest, so the GET endpoints are cheap reads.

**Measurement flow.** For each feature: Polygon/MultiPolygon → area, hectares, perimeter; LineString/MultiLineString → length; Point → `NOT_APPLICABLE`; everything else → `UNSUPPORTED`. Self-intersecting polygons are repaired with `make_valid` before measuring and the reason is reported in `message`.

**CRS handling.** Shapefile CRS comes from the `.prj`; KML is always WGS84 (EPSG:4326). Degrees are never used for area/length. Per feature: (1) transform to WGS84 to find its centroid, (2) pick that location's **UTM zone** (`326xx` north / `327xx` south; polar UPS `32661/32761` beyond 84°N/80°S), (3) transform the original geometry directly into it, (4) measure in metres and report the `measurement_crs` used. `always_xy=True` is used everywhere so lon/lat axis-order quirks cannot flip coordinates. This applies even to files already projected, since an arbitrary projected CRS (e.g. Web Mercator) can distort badly.

## Design decisions & alternatives

- **pyshp + lxml instead of GeoPandas/GDAL/Fiona.** GDAL is the usual install pain point; these libraries are pure wheels and cover exactly the two formats required. Trade-off: fewer formats than GDAL (GeoJSON, GPKG, KMZ would need either extra readers or switching to pyogrio).
- **UTM per feature vs. alternatives.** Alternatives: one CRS per file (simpler, but wrong for files spanning zones), geodesic maths on the ellipsoid (most accurate, but the brief asks for a projected CRS), or local equal-area projection (best for area). UTM is well-known, accurate to ~0.1% inside a zone, and lets length and area share one pipeline. Known limit: very large features spanning several zones or the antimeridian lose accuracy.
- **Synchronous processing.** Simple and deterministic for files of tens of MB; the status field and `409` handling are already in place so moving to a background worker is a contained change.
- **Store results in a DB** (SQLite → swap `DATABASE_URL` for PostgreSQL) rather than re-parsing the file per request; geometry stored as GeoJSON (no PostGIS dependency).
- **Security.** Only recognised shapefile component files are extracted and member names are never used as paths (no zip-slip); uncompressed size capped (zip bombs); KML parsed with entity resolution/network disabled (XXE); upload size capped.
- **Partial failure.** Unparsable or unsupported features are reported per feature, not as a request failure.

## Learnings
- Axis order (lat/lon vs lon/lat) is the classic CRS bug; `always_xy=True` removes it. Shapefile ring orientation (clockwise = outer, counter-clockwise = hole) matters when building test data.
- Writing tests against an *independent* reference (pyproj `Geod` geodesic area) catches projection mistakes far better than hard-coded numbers.
- Test fixtures need realistic coordinates: a "UTM 43N" polygon at (0,0) is actually in another zone and measured differently.

## Future scope
GeoJSON/GeoPackage/KMZ support; background processing (Celery/RQ) with progress; PostGIS storage and spatial queries; equal-area or geodesic cross-check mode and unit selection (acres, km²); auth and per-user files; CSV/GeoJSON export of measurements; Alembic migrations; Docker image and CI.
