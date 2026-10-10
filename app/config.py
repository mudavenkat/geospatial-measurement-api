import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./geoapi.db"))
    max_upload_mb: int = field(default_factory=lambda: int(os.getenv("MAX_UPLOAD_MB", "50")))
    # Guard against zip bombs: cap the total uncompressed size we are willing to extract.
    max_unzipped_mb: int = field(default_factory=lambda: int(os.getenv("MAX_UNZIPPED_MB", "500")))
