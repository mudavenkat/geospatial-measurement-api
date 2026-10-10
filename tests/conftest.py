import io
import zipfile

import pytest
import shapefile
from fastapi.testclient import TestClient
from pyproj import CRS

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(Settings(database_url=f"sqlite:///{tmp_path / 't.db'}", max_upload_mb=1)))


def make_shapefile_zip(shape_type, shapes, records, crs: CRS | None = CRS.from_epsg(4326), fields=(("name", "C", 40),)) -> bytes:
    """Build an in-memory zipped shapefile. `shapes` are callables taking the writer."""
    parts = {k: io.BytesIO() for k in ("shp", "shx", "dbf")}
    w = shapefile.Writer(shp=parts["shp"], shx=parts["shx"], dbf=parts["dbf"], shapeType=shape_type)
    for f in fields:
        w.field(*f)
    for add, rec in zip(shapes, records):
        add(w)
        w.record(*rec)
    w.close()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for ext, b in parts.items():
            z.writestr(f"survey.{ext}", b.getvalue())
        if crs is not None:
            z.writestr("survey.prj", crs.to_wkt(version="WKT1_ESRI"))
    return buf.getvalue()


def upload(client, name, data, **form):
    return client.post("/api/files/", files={"file": (name, data)}, data=form)
