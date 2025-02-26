import os
import pathlib
import sqlite3

BASE_DIR = pathlib.Path(__file__).resolve().parent
INTERNAL_ISIN_DB_PATH = BASE_DIR / "isin.db"


def get_isin_db_path():
    env_isin_path = os.getenv("CASPARSER_ISIN_DB")
    try:
        if os.path.exists(env_isin_path) and os.path.isfile(env_isin_path):
            return pathlib.Path(env_isin_path)
    except TypeError:
        pass
    return INTERNAL_ISIN_DB_PATH


def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


class DB:
    """Base class for database queries."""

    connection = None
    cursor = None

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
