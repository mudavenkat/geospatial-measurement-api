import shutil
import tempfile
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pyproj import CRS
from pyproj.exceptions import CRSError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Feature, FileStatus, UploadedFile
from ..schemas import FeatureOut, FeaturesPage, FileOut, MeasurementOut, MeasurementsPage
from ..services import ingest, readers

router = APIRouter(prefix="/api/files")


def get_db(request: Request):
    with request.app.state.session_factory() as db:
        yield db


def _file_or_404(db: Session, file_id: str) -> UploadedFile:
    f = db.get(UploadedFile, file_id)
    if f is None:
        raise HTTPException(404, "File not found")
    return f


def _file_out(db: Session, f: UploadedFile) -> FileOut:
    rows = db.execute(
        select(Feature.geometry_type, func.count()).where(Feature.file_id == f.id).group_by(Feature.geometry_type)
    ).all()
    return FileOut(
        id=f.id, filename=f.filename, file_type=f.file_type, feature_count=f.feature_count, crs=f.crs,
        status=f.status, error=f.error, created_at=f.created_at,
        geometry_types={(t or "None"): n for t, n in rows},
    )


def _detect_type(filename: str) -> str:
    name = filename.lower()
    if name.endswith(".zip"):
        return "shapefile"
    if name.endswith(".kml"):
        return "kml"
    raise HTTPException(400, "Unsupported file type. Upload a .zip (containing a Shapefile) or a .kml file.")


@router.post("/", status_code=201, response_model=FileOut)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    assume_crs: str | None = Form(None, description="CRS to use if a Shapefile has no .prj, e.g. EPSG:4326"),
    db: Session = Depends(get_db),
):
    """Upload a zipped Shapefile or a KML. The file is parsed and measured before the response is returned."""
    settings = request.app.state.settings
    filename = Path(file.filename or "upload").name
    ftype = _detect_type(filename)

    assumed = None
    if assume_crs:
        try:
            assumed = CRS.from_user_input(assume_crs)
        except CRSError:
            raise HTTPException(400, f"Unrecognised assume_crs value: {assume_crs!r}")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / ("upload.zip" if ftype == "shapefile" else "upload.kml")
        limit = settings.max_upload_mb * 1024 * 1024
        size = 0
        with open(path, "wb") as out:  # stream to disk, enforcing the size limit
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"File too large (max {settings.max_upload_mb} MB)")
                out.write(chunk)

        record = UploadedFile(filename=filename, file_type=ftype)
        db.add(record)
        db.commit()
        try:
            ingest.process_upload(db, record, path, settings.max_unzipped_mb * 1024 * 1024, assumed)
        except readers.FileReadError as exc:
            return JSONResponse(status_code=422, content={"detail": str(exc), "id": record.id, "status": "FAILED"})
    return _file_out(db, record)


@router.get("/{file_id}/", response_model=FileOut)
def get_file(file_id: str, db: Session = Depends(get_db)):
    return _file_out(db, _file_or_404(db, file_id))


def _require_completed(f: UploadedFile):
    if f.status != FileStatus.COMPLETED:
        raise HTTPException(409, f"File is {f.status.value}" + (f": {f.error}" if f.error else ""))


@router.get("/{file_id}/features/", response_model=FeaturesPage)
def get_features(file_id: str, limit: int = Query(1000, ge=1, le=10000), offset: int = Query(0, ge=0),
                 db: Session = Depends(get_db)):
    """Every feature: index, geometry type, GeoJSON geometry (in the file's CRS), CRS and properties."""
    f = _file_or_404(db, file_id)
    _require_completed(f)
    rows = db.scalars(select(Feature).where(Feature.file_id == file_id).order_by(Feature.index)
                      .limit(limit).offset(offset)).all()
    return FeaturesPage(file_id=file_id, total=f.feature_count, limit=limit, offset=offset, features=[
        FeatureOut(id=r.index, geometry_type=r.geometry_type, geometry=r.geometry, crs=f.crs, properties=r.properties)
        for r in rows
    ])


@router.get("/{file_id}/measurements/", response_model=MeasurementsPage)
def get_measurements(file_id: str, limit: int = Query(1000, ge=1, le=10000), offset: int = Query(0, ge=0),
                     db: Session = Depends(get_db)):
    f = _file_or_404(db, file_id)
    _require_completed(f)
    rows = db.scalars(select(Feature).where(Feature.file_id == file_id).order_by(Feature.index)
                      .limit(limit).offset(offset)).all()

    totals: Counter = Counter()  # file-wide, not just this page
    for m in db.scalars(select(Feature.measurements).where(Feature.file_id == file_id, Feature.measurement_status == "OK")):
        for k in ("area_sq_m", "area_hectares", "length_m"):
            if k in m:
                totals[k] += m[k]
    return MeasurementsPage(
        file_id=file_id, source_crs=f.crs, total=f.feature_count, limit=limit, offset=offset,
        totals={k: round(v, 4) for k, v in totals.items()},
        measurements=[MeasurementOut(
            feature_id=r.index, geometry_type=r.geometry_type, measurement_status=r.measurement_status,
            measurements=r.measurements, measurement_crs=r.measurement_crs, message=r.message) for r in rows],
    )
