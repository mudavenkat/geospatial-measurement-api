import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class FileStatus(str, enum.Enum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class UploadedFile(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(20))  # "shapefile" | "kml"
    status: Mapped[FileStatus] = mapped_column(Enum(FileStatus), default=FileStatus.PROCESSING)
    crs: Mapped[str | None] = mapped_column(String(100), nullable=True)
    feature_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    features: Mapped[list["Feature"]] = relationship(
        back_populates="file", cascade="all, delete-orphan", order_by="Feature.index"
    )


class Feature(Base):
    __tablename__ = "features"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id"), index=True)
    index: Mapped[int] = mapped_column(Integer)  # position within the file (0-based)
    geometry_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    geometry: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # GeoJSON, in the file's own CRS
    properties: Mapped[dict] = mapped_column(JSON, default=dict)
    measurement_status: Mapped[str] = mapped_column(String(20))  # OK | NOT_APPLICABLE | UNSUPPORTED | ERROR
    measurements: Mapped[dict] = mapped_column(JSON, default=dict)
    measurement_crs: Mapped[str | None] = mapped_column(String(30), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)  # reason / warnings

    file: Mapped[UploadedFile] = relationship(back_populates="features")
