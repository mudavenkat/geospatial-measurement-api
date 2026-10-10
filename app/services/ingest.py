"""Orchestrates: read file -> build Feature rows (with measurements) -> persist."""
from pathlib import Path

from pyproj import CRS
from shapely.geometry import mapping
from sqlalchemy.orm import Session

from ..models import Feature, FileStatus, UploadedFile
from . import readers
from .crs import crs_label
from .measure import measure_geometry


def process_upload(db: Session, record: UploadedFile, path: Path, max_unzipped: int, assume_crs: CRS | None = None) -> UploadedFile:
    """Fill `record` from the file at `path`. On a read error the record is marked FAILED and the error re-raised."""
    try:
        if record.file_type == "shapefile":
            crs, raw = readers.read_shapefile_zip(path, max_unzipped, assume_crs)
        else:
            crs, raw = readers.read_kml(path)
    except readers.FileReadError as exc:
        record.status, record.error = FileStatus.FAILED, str(exc)
        db.commit()
        raise

    record.crs = crs_label(crs)
    for rf in raw:
        m = measure_geometry(rf.geometry, crs)
        message = "; ".join(x for x in (rf.warning, m.message) if x) or None
        record.features.append(Feature(
            index=rf.index,
            geometry_type=rf.geometry.geom_type if rf.geometry is not None else None,
            geometry=mapping(rf.geometry) if rf.geometry is not None and not rf.geometry.is_empty else None,
            properties=rf.properties,
            measurement_status=m.status,
            measurements=m.values,
            measurement_crs=m.measurement_crs,
            message=message,
        ))
    record.feature_count = len(raw)
    record.status = FileStatus.COMPLETED
    db.commit()
    return record
