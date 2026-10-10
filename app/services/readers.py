"""Readers that turn an uploaded file into (crs, [RawFeature])."""
import datetime as dt
import decimal
import tempfile
import zipfile
from struct import error as struct_error
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import shapefile  # pyshp
from lxml import etree
from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely.geometry import (GeometryCollection, LineString, MultiLineString, MultiPoint,
                              MultiPolygon, Point, Polygon, shape)
from shapely.geometry.base import BaseGeometry

SHP_PARTS = {".shp", ".shx", ".dbf", ".prj", ".cpg"}


class FileReadError(Exception):
    """The upload is not a readable/valid geospatial file (maps to HTTP 422)."""


@dataclass
class RawFeature:
    index: int
    geometry: BaseGeometry | None
    properties: dict
    warning: str | None = None


def json_safe(value):
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


# ----------------------------------------------------------------------------- Shapefile
def _extract_shapefile_parts(zip_path: Path, dest: Path, max_unzipped: int) -> Path:
    """Extract only the shapefile component files, flattened into `dest`.

    Member names are never used as paths (only their suffix), which rules out
    zip-slip; total uncompressed size is capped to defuse zip bombs.
    """
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise FileReadError("File is not a valid ZIP archive") from exc
    with zf:
        members = [m for m in zf.infolist()
                   if not m.is_dir() and "__MACOSX" not in m.filename
                   and PurePosixPath(m.filename).suffix.lower() in SHP_PARTS]
        if sum(m.file_size for m in members) > max_unzipped:
            raise FileReadError("ZIP contents exceed the allowed uncompressed size")
        shps = [m for m in members if m.filename.lower().endswith(".shp")]
        if not shps:
            raise FileReadError("ZIP does not contain a .shp file")
        if len(shps) > 1:
            raise FileReadError("ZIP contains more than one shapefile; upload one layer per file")
        stem = PurePosixPath(shps[0].filename).with_suffix("")
        base = "layer"
        for m in members:
            p = PurePosixPath(m.filename)
            # keep only siblings of the .shp (same folder + stem), case-insensitive
            if p.with_suffix("").as_posix().lower() == stem.as_posix().lower():
                with zf.open(m) as src, open(dest / f"{base}{p.suffix.lower()}", "wb") as out:
                    out.write(src.read())
    for needed in (".shx", ".dbf"):
        if not (dest / f"{base}{needed}").exists():
            raise FileReadError(f"Shapefile is missing its {needed} component")
    return dest / f"{base}.shp"


def read_shapefile_zip(zip_path: Path, max_unzipped: int, assume_crs: CRS | None = None) -> tuple[CRS, list[RawFeature]]:
    with tempfile.TemporaryDirectory() as tmp:
        shp = _extract_shapefile_parts(zip_path, Path(tmp), max_unzipped)

        crs = assume_crs
        prj = shp.with_suffix(".prj")
        if prj.exists():
            try:
                crs = CRS.from_wkt(prj.read_text(errors="replace"))
            except CRSError as exc:
                if crs is None:
                    raise FileReadError(f"Could not parse the .prj coordinate system: {exc}") from exc
        if crs is None:
            raise FileReadError(
                "Shapefile has no .prj, so its CRS is unknown. Include the .prj or pass assume_crs (e.g. EPSG:4326)."
            )

        encoding = "utf-8"
        cpg = shp.with_suffix(".cpg")
        if cpg.exists():
            encoding = cpg.read_text(errors="ignore").strip() or "utf-8"
        try:
            reader = shapefile.Reader(str(shp), encoding=encoding, encodingErrors="replace")
        except (shapefile.ShapefileException, LookupError, OSError) as exc:
            raise FileReadError(f"Could not read shapefile: {exc}") from exc

        features: list[RawFeature] = []
        with reader:
            fields = [f[0] for f in reader.fields[1:]]
            try:
                for i, sr in enumerate(reader.iterShapeRecords()):
                    props = {k: json_safe(v) for k, v in zip(fields, list(sr.record))}
                    geom, warning = None, None
                    try:
                        if sr.shape.shapeType != shapefile.NULL:
                            geom = shape(sr.shape.__geo_interface__)
                    except Exception as exc:  # e.g. MultiPatch: keep the feature, flag it
                        warning = f"could not convert geometry: {exc}"
                    features.append(RawFeature(i, geom, props, warning))
            except (shapefile.ShapefileException, struct_error, ValueError) as exc:
                raise FileReadError(f"Shapefile is corrupt: {exc}") from exc
        return crs, features



# ----------------------------------------------------------------------------- KML
KML_CRS = CRS.from_epsg(4326)  # KML is always WGS84 lon/lat


def _local(tag) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _children(el, name):
    return [c for c in el if _local(c.tag) == name]


def _coords(el) -> list[tuple]:
    node = next((c for c in el.iter() if _local(c.tag) == "coordinates"), None)
    if node is None or not (node.text or "").strip():
        raise ValueError("missing coordinates")
    pts = []
    for tok in node.text.split():
        parts = tok.split(",")
        if len(parts) < 2:
            raise ValueError(f"bad coordinate {tok!r}")
        pts.append(tuple(float(p) for p in parts[:3]))
    return pts


def _kml_geometry(el) -> BaseGeometry:
    name = _local(el.tag)
    if name == "Point":
        return Point(_coords(el)[0])
    if name in ("LineString", "LinearRing"):
        return LineString(_coords(el))
    if name == "Polygon":
        outer = next(c for c in el if _local(c.tag) == "outerBoundaryIs")
        holes = [_coords(h) for h in _children(el, "innerBoundaryIs")]
        return Polygon(_coords(outer), holes)
    if name == "MultiGeometry":
        parts = [_kml_geometry(c) for c in el if _local(c.tag) in
                 ("Point", "LineString", "LinearRing", "Polygon", "MultiGeometry")]
        kinds = {p.geom_type for p in parts}
        if kinds == {"Point"}:
            return MultiPoint(parts)
        if kinds == {"LineString"}:
            return MultiLineString(parts)
        if kinds == {"Polygon"}:
            return MultiPolygon(parts)
        return GeometryCollection(parts)
    raise ValueError(f"unsupported KML geometry <{name}>")


_KML_GEOMS = {"Point", "LineString", "LinearRing", "Polygon", "MultiGeometry"}


def read_kml(path: Path) -> tuple[CRS, list[RawFeature]]:
    # Hardened parser: no entity resolution / network access (XXE, billion-laughs).
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    try:
        root = etree.parse(str(path), parser).getroot()
    except etree.XMLSyntaxError as exc:
        raise FileReadError(f"Invalid KML/XML: {exc}") from exc
    if _local(root.tag) != "kml" and not any(_local(e.tag) == "Placemark" for e in root.iter()):
        raise FileReadError("File is not a KML document")

    features: list[RawFeature] = []
    for i, pm in enumerate(e for e in root.iter() if _local(e.tag) == "Placemark"):
        props: dict = {}
        for child in pm:
            n = _local(child.tag)
            if n in ("name", "description") and child.text and child.text.strip():
                props[n] = child.text.strip()
            elif n == "ExtendedData":
                for d in child.iter():
                    if _local(d.tag) == "Data" and d.get("name"):
                        v = next((x.text for x in d if _local(x.tag) == "value"), None)
                        props[d.get("name")] = v
                    elif _local(d.tag) == "SimpleData" and d.get("name"):
                        props[d.get("name")] = d.text
        geom_el = next((c for c in pm if _local(c.tag) in _KML_GEOMS), None)
        geom, warning = None, None
        if geom_el is None:
            warning = "placemark has no supported geometry"
        else:
            try:
                geom = _kml_geometry(geom_el)
            except Exception as exc:
                warning = f"could not parse geometry: {exc}"
        features.append(RawFeature(i, geom, props, warning))
    return KML_CRS, features
