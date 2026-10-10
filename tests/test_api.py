import io
import zipfile

import pytest
import shapefile
from pyproj import CRS, Geod
from shapely.geometry import Polygon as ShPolygon, LineString as ShLine

from app.services.crs import utm_epsg_for
from tests.conftest import make_shapefile_zip, upload

# 0.01° x 0.01° square in Bengaluru
BLR = [(77.59, 12.97), (77.60, 12.97), (77.60, 12.98), (77.59, 12.98), (77.59, 12.97)]
GEOD = Geod(ellps="WGS84")


def poly(w, ring=BLR):
    w.poly([ring])


def kml(*placemarks):
    return ('<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            + "".join(placemarks) + "</Document></kml>").encode()


def pm(name, geom, extra=""):
    return f"<Placemark><name>{name}</name>{extra}{geom}</Placemark>"


KML_POLY = "<Polygon><outerBoundaryIs><LinearRing><coordinates>" + " ".join(f"{x},{y},0" for x, y in BLR) + "</coordinates></LinearRing></outerBoundaryIs></Polygon>"
KML_LINE = "<LineString><coordinates>77.59,12.97 77.60,12.97</coordinates></LineString>"
KML_POINT = "<Point><coordinates>77.59,12.97</coordinates></Point>"


# ---------------- CRS selection ----------------
@pytest.mark.parametrize("lon,lat,epsg", [(77.59, 12.97, 32643), (151.2, -33.9, 32756), (-0.12, 51.5, 32630),
                                          (10, 89, 32661), (10, -85, 32761), (179.9, 0, 32660), (180, 0, 32601)])
def test_utm_zone_selection(lon, lat, epsg):
    assert utm_epsg_for(lon, lat) == epsg


# ---------------- Shapefile upload & measurements ----------------
def test_shapefile_polygon_area_matches_geodesic_reference(client):
    data = make_shapefile_zip(shapefile.POLYGON, [poly], [("plot-1",)])
    r = upload(client, "survey.zip", data)
    assert r.status_code == 201
    body = r.json()
    assert body["filename"] == "survey.zip" and body["feature_count"] == 1
    assert body["crs"] == "EPSG:4326" and body["status"] == "COMPLETED"
    assert body["geometry_types"] == {"Polygon": 1}

    m = client.get(f"/api/files/{body['id']}/measurements/").json()["measurements"][0]
    ref_area, ref_perimeter = (abs(v) for v in GEOD.polygon_area_perimeter(*zip(*BLR[:-1])))
    assert m["measurement_status"] == "OK" and m["measurement_crs"] == "EPSG:32643"
    assert m["measurements"]["area_sq_m"] == pytest.approx(ref_area, rel=0.002)       # ~1.2 km², within 0.2%
    assert m["measurements"]["perimeter_m"] == pytest.approx(ref_perimeter, rel=0.002)
    assert m["measurements"]["area_sq_m"] > 1_000_000  # i.e. metres, not "0.0001 degrees²"


def test_shapefile_projected_crs_is_measured_correctly(client):
    utm = CRS.from_epsg(32643)  # file already in metres
    sq = [(500000, 1434000), (500100, 1434000), (500100, 1434200), (500000, 1434200), (500000, 1434000)]
    data = make_shapefile_zip(shapefile.POLYGON, [lambda w: w.poly([sq])], [("p",)], crs=utm)
    fid = upload(client, "utm.zip", data).json()["id"]
    assert client.get(f"/api/files/{fid}/").json()["crs"] == "EPSG:32643"
    m = client.get(f"/api/files/{fid}/measurements/").json()["measurements"][0]["measurements"]
    assert m["area_sq_m"] == pytest.approx(100 * 200, abs=0.5)
    assert m["perimeter_m"] == pytest.approx(600, abs=0.5)


def test_shapefile_polygon_with_hole_subtracts_hole(client):
    X, Y = 500000, 1434000                                              # realistic UTM 43N coordinates
    outer = [(X, Y), (X, Y + 100), (X + 100, Y + 100), (X + 100, Y), (X, Y)]   # clockwise = exterior in shapefiles
    hole = [(X + 25, Y + 25), (X + 75, Y + 25), (X + 75, Y + 75), (X + 25, Y + 75), (X + 25, Y + 25)]  # counter-clockwise = hole
    data = make_shapefile_zip(shapefile.POLYGON, [lambda w: w.poly([outer, hole])], [("h",)], crs=CRS.from_epsg(32643))
    fid = upload(client, "h.zip", data).json()["id"]
    m = client.get(f"/api/files/{fid}/measurements/").json()["measurements"][0]["measurements"]
    assert m["area_sq_m"] == pytest.approx(10000 - 2500, abs=0.5)


def test_shapefile_linestring_length(client):
    line = [(77.59, 12.97), (77.60, 12.97)]
    data = make_shapefile_zip(shapefile.POLYLINE, [lambda w: w.line([line])], [("road",)])
    fid = upload(client, "roads.zip", data).json()["id"]
    m = client.get(f"/api/files/{fid}/measurements/").json()["measurements"][0]["measurements"]
    ref = GEOD.line_length(*zip(*line))
    assert m["length_m"] == pytest.approx(ref, rel=0.002)


def test_shapefile_points_have_no_measurement_and_dont_crash(client):
    data = make_shapefile_zip(shapefile.POINT, [lambda w: w.point(77.59, 12.97), lambda w: w.point(77.6, 12.9)], [("a",), ("b",)])
    fid = upload(client, "pts.zip", data).json()["id"]
    ms = client.get(f"/api/files/{fid}/measurements/").json()["measurements"]
    assert [m["measurement_status"] for m in ms] == ["NOT_APPLICABLE", "NOT_APPLICABLE"]
    assert all(m["measurements"] == {} for m in ms)


def test_unsupported_geometry_is_handled_gracefully(client):
    data = make_shapefile_zip(shapefile.MULTIPOINT, [lambda w: w.multipoint([(77.59, 12.97), (77.6, 12.9)])], [("mp",)])
    fid = upload(client, "mp.zip", data).json()["id"]
    m = client.get(f"/api/files/{fid}/measurements/").json()["measurements"][0]
    assert m["measurement_status"] == "UNSUPPORTED" and "MultiPoint" in m["message"]


def test_features_endpoint_returns_geometry_crs_and_properties(client):
    data = make_shapefile_zip(shapefile.POLYGON, [poly], [("plot-1",)])
    fid = upload(client, "survey.zip", data).json()["id"]
    feat = client.get(f"/api/files/{fid}/features/").json()["features"][0]
    assert feat["id"] == 0 and feat["geometry_type"] == "Polygon" and feat["crs"] == "EPSG:4326"
    assert feat["properties"] == {"name": "plot-1"}
    assert feat["geometry"]["type"] == "Polygon" and len(feat["geometry"]["coordinates"][0]) == 5


def test_shapefile_without_prj_rejected_unless_assume_crs_given(client):
    data = make_shapefile_zip(shapefile.POLYGON, [poly], [("p",)], crs=None)
    r = upload(client, "noprj.zip", data)
    assert r.status_code == 422 and ".prj" in r.json()["detail"]
    assert client.get(f"/api/files/{r.json()['id']}/").json()["status"] == "FAILED"
    ok = upload(client, "noprj.zip", data, assume_crs="EPSG:4326")
    assert ok.status_code == 201 and ok.json()["crs"] == "EPSG:4326"


# ---------------- KML ----------------
def test_kml_mixed_features(client):
    data = kml(pm("Plot", KML_POLY, "<ExtendedData><Data name='owner'><value>Asha</value></Data></ExtendedData>"),
               pm("Road", KML_LINE), pm("Gate", KML_POINT))
    body = upload(client, "survey.kml", data).json()
    assert body["feature_count"] == 3 and body["crs"] == "EPSG:4326" and body["status"] == "COMPLETED"
    assert body["geometry_types"] == {"Polygon": 1, "LineString": 1, "Point": 1}

    ms = client.get(f"/api/files/{body['id']}/measurements/").json()
    assert [m["measurement_status"] for m in ms["measurements"]] == ["OK", "OK", "NOT_APPLICABLE"]
    assert ms["measurements"][0]["measurements"]["area_sq_m"] == pytest.approx(abs(GEOD.polygon_area_perimeter(*zip(*BLR[:-1]))[0]), rel=0.002)
    assert ms["totals"]["length_m"] == pytest.approx(ms["measurements"][1]["measurements"]["length_m"])
    feats = client.get(f"/api/files/{body['id']}/features/").json()["features"]
    assert feats[0]["properties"] == {"name": "Plot", "owner": "Asha"}


def test_kml_multigeometry_and_unsupported_collection(client):
    shifted = KML_POLY.replace("77.59", "77.69").replace("77.6,", "77.7,")   # a second, non-overlapping plot
    two_polys = f"<MultiGeometry>{KML_POLY}{shifted}</MultiGeometry>"
    mixed = f"<MultiGeometry>{KML_POLY}{KML_LINE}</MultiGeometry>"
    fid = upload(client, "m.kml", kml(pm("mp", two_polys), pm("gc", mixed))).json()["id"]
    ms = client.get(f"/api/files/{fid}/measurements/").json()["measurements"]
    single = abs(GEOD.polygon_area_perimeter(*zip(*BLR[:-1]))[0])
    assert ms[0]["geometry_type"] == "MultiPolygon"
    assert ms[0]["measurements"]["area_sq_m"] == pytest.approx(2 * single, rel=0.002)
    assert ms[1]["geometry_type"] == "GeometryCollection" and ms[1]["measurement_status"] == "UNSUPPORTED"


def test_kml_bad_feature_does_not_fail_whole_file(client):
    bad = "<Point><coordinates>oops</coordinates></Point>"
    fid = upload(client, "b.kml", kml(pm("bad", bad), pm("good", KML_LINE))).json()["id"]
    ms = client.get(f"/api/files/{fid}/measurements/").json()["measurements"]
    assert ms[0]["measurement_status"] == "UNSUPPORTED" and "could not parse" in ms[0]["message"]
    assert ms[1]["measurement_status"] == "OK"


def test_kml_xxe_is_not_expanded(client):
    evil = (b'<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
            b'<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>&x;</name>'
            b'<Point><coordinates>1,1</coordinates></Point></Placemark></Document></kml>')
    r = upload(client, "evil.kml", evil)
    if r.status_code == 201:
        feats = client.get(f"/api/files/{r.json()['id']}/features/").json()["features"]
        assert "root:" not in str(feats)


# ---------------- Info endpoint & errors ----------------
def test_file_info_matches_spec_shape(client):
    fid = upload(client, "survey.kml", kml(pm("a", KML_POINT))).json()["id"]
    info = client.get(f"/api/files/{fid}/").json()
    assert {"id": fid, "filename": "survey.kml", "feature_count": 1, "crs": "EPSG:4326", "status": "COMPLETED"}.items() <= info.items()


def test_unknown_file_404_everywhere(client):
    for path in ("", "features/", "measurements/"):
        assert client.get(f"/api/files/nope/{path}").status_code == 404


def test_rejects_unsupported_extension(client):
    assert upload(client, "data.geojson", b"{}").status_code == 400


def test_rejects_corrupt_zip_and_zip_without_shp(client):
    assert upload(client, "x.zip", b"not a zip").status_code == 422
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "hi")
    r = upload(client, "x.zip", buf.getvalue())
    assert r.status_code == 422 and ".shp" in r.json()["detail"]


def test_rejects_malformed_kml_and_non_kml_xml(client):
    assert upload(client, "x.kml", b"<kml><oops").status_code == 422
    assert upload(client, "x.kml", b"<html></html>").status_code == 422


def test_failed_file_measurements_return_409(client):
    r = upload(client, "x.kml", b"<kml><oops")
    assert client.get(f"/api/files/{r.json()['id']}/measurements/").status_code == 409


def test_oversized_upload_rejected(client):  # fixture limit is 1 MB
    assert upload(client, "big.kml", b"x" * (1024 * 1024 + 10)).status_code == 413


def test_zip_slip_member_names_cannot_escape(client, tmp_path):
    good = make_shapefile_zip(shapefile.POINT, [lambda w: w.point(1, 1)], [("a",)])
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(good)) as src, zipfile.ZipFile(buf, "w") as dst:
        for n in src.namelist():
            dst.writestr("../../evil/" + n, src.read(n))
    r = upload(client, "slip.zip", buf.getvalue())
    assert r.status_code == 201
    assert not (tmp_path.parent / "evil").exists()


def test_pagination_and_file_wide_totals(client):
    sq = lambda i: (lambda w: w.poly([[(500000 + i, 1434000), (500000 + i, 1434010), (500000 + i + 10, 1434010), (500000 + i + 10, 1434000), (500000 + i, 1434000)]]))
    data = make_shapefile_zip(shapefile.POLYGON, [sq(i * 20) for i in range(5)], [(f"p{i}",) for i in range(5)], crs=CRS.from_epsg(32643))
    fid = upload(client, "many.zip", data).json()["id"]
    page = client.get(f"/api/files/{fid}/measurements/?limit=2&offset=3").json()
    assert page["total"] == 5 and [m["feature_id"] for m in page["measurements"]] == [3, 4]
    assert page["totals"]["area_sq_m"] == pytest.approx(500, abs=0.5)  # all 5, not just this page
