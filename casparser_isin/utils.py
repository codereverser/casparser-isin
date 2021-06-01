import os
import pathlib

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
