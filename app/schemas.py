from datetime import datetime

from pydantic import BaseModel

from .models import FileStatus


class FileOut(BaseModel):
    id: str
    filename: str
    file_type: str
    feature_count: int
    crs: str | None
    status: FileStatus
    error: str | None = None
    geometry_types: dict[str, int] = {}
    created_at: datetime


class FeatureOut(BaseModel):
    id: int  # feature index within the file (0-based)
    geometry_type: str | None
    geometry: dict | None
    crs: str | None
    properties: dict


class FeaturesPage(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    features: list[FeatureOut]


class MeasurementOut(BaseModel):
    feature_id: int
    geometry_type: str | None
    measurement_status: str  # OK | NOT_APPLICABLE | UNSUPPORTED | ERROR
    measurements: dict[str, float]
    measurement_crs: str | None
    message: str | None


class MeasurementsPage(BaseModel):
    file_id: str
    source_crs: str | None
    total: int
    limit: int
    offset: int
    totals: dict[str, float]  # file-wide sums over all measurable features
    measurements: list[MeasurementOut]
