from fastapi import FastAPI

from .config import Settings
from .database import Base, make_engine, make_session_factory
from .routers import files


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)

    app = FastAPI(title="Geospatial File Measurement API", version="1.0.0")
    app.state.settings = settings
    app.state.session_factory = make_session_factory(engine)
    app.include_router(files.router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app
