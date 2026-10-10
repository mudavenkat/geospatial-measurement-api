from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    return create_engine(url, **kwargs)


def make_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
