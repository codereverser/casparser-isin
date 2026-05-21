import logging
import os
import pathlib
import sqlite3

logger = logging.getLogger(__name__)

BASE_DIR = pathlib.Path(__file__).resolve().parent
INTERNAL_ISIN_DB_PATH = BASE_DIR / "isin.db"


def get_isin_db_path() -> pathlib.Path:
    """
    Resolve the ISIN database path.

    Order of precedence:

    1. ``CASPARSER_ISIN_DB`` env var, if it points at an existing file.
    2. The DB bundled with the wheel at :data:`INTERNAL_ISIN_DB_PATH`.

    If the env var is set but unusable (missing file, a directory, etc.) a
    warning is logged and the bundled DB is returned.
    """
    env_isin_path = os.getenv("CASPARSER_ISIN_DB")
    if env_isin_path:
        candidate = pathlib.Path(env_isin_path)
        if candidate.is_file():
            return candidate
        logger.warning(
            "CASPARSER_ISIN_DB is set to %r but the path is not a readable file; "
            "falling back to bundled database at %s",
            env_isin_path,
            INTERNAL_ISIN_DB_PATH,
        )
    return INTERNAL_ISIN_DB_PATH


def dict_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


class DB:
    """Base class for database queries."""

    def __init__(self):
        self.connection: sqlite3.Connection | None = None
        self.cursor: sqlite3.Cursor | None = None

    def __enter__(self):
        self.initialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def initialize(self):
        """Initialize database."""
        self.connection = sqlite3.connect(get_isin_db_path())
        self.connection.row_factory = dict_factory
        self.cursor = self.connection.cursor()

    def close(self):
        """Close database connection."""
        if self.cursor is not None:
            self.cursor.close()
            self.cursor = None
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def run_query(self, sql, arguments, fetchone=False):
        self_initialized = False
        if self.connection is None:
            self.initialize()
            self_initialized = True
        try:
            self.cursor.execute(sql, arguments)
            if fetchone:
                return self.cursor.fetchone()
            return self.cursor.fetchall()
        finally:
            if self_initialized:
                self.close()
