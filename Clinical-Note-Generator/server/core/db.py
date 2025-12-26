# C:\Clinical-Note-Generator\server\core\db.py
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from .config import get_settings

settings = get_settings()

connect_args = {}
engine_kwargs = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False
    if ":memory:" in settings.database_url:
        engine_kwargs["poolclass"] = StaticPool

engine = create_engine(
    settings.database_url,
    echo=False,
    connect_args=connect_args,
    **engine_kwargs,
)


def init_db() -> None:
    # Import models to ensure metadata is registered
    from ..models import refresh_token  # noqa: F401
    from ..models import user  # noqa: F401
    from ..models import workspace  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
