from contextlib import contextmanager
import psycopg
from .config import settings

@contextmanager
def connect():
    url = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as conn:
        yield conn
