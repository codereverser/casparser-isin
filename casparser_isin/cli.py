import argparse
import pathlib
import sqlite3
from urllib.request import urlopen

from .__version__ import __version__

BASE_DIR = pathlib.Path(__file__).resolve().parent
ISIN_DB_PATH = BASE_DIR / "isin.db"

META_URL = (
    "https://raw.githubusercontent.com/codereverser/casparser-isin/main/casparser_isin/isin.db.meta"
)
DB_URL = "https://raw.githubusercontent.com/codereverser/casparser-isin/main/casparser_isin/isin.db"


def get_metadata():
    conn = sqlite3.connect(ISIN_DB_PATH)
    cursor = conn.cursor()
    try:
        with conn:
            cursor.execute("SELECT key, value from meta")
            metadata = dict(cursor.fetchall())
            metadata["cli-version"] = __version__
            return metadata
    finally:
        cursor.close()
        conn.close()


def print_version():
    metadata = get_metadata()
    print(f"cli-version : {metadata['cli-version']}")
    print(f"db-version  : {metadata['version']}")
    print(f"db-format   : {metadata['dbformat']}")


def update_isin_db():
    local_meta = get_metadata()
    print("Fetching remote isin db metadata")
    response = urlopen(META_URL)
    data = response.read().decode()
    remote_meta = {}
    for line in data.splitlines():
        split = line.split("=")
        if len(split) == 2:
            k, v = split
            remote_meta[k.strip()] = v.strip()
    print("Local db version  : ", local_meta.get("version"))
    print("Remote db version : ", remote_meta.get("version"))
    if (
        remote_meta["version"] > local_meta["version"]
        and remote_meta["dbformat"] != local_meta["dbformat"]
    ):
        print("Fetching latest isin.db")
        response = urlopen(DB_URL)
        data = response.read()
        with open(ISIN_DB_PATH, "wb") as f:
            f.write(data)
    else:
        print("isin database is upto date")


def main():
    parser = argparse.ArgumentParser("casparser-isin", description="casparser-isin cli")
    parser.add_argument("-v", "--version", help="Print version and exit", action="store_true")
    parser.add_argument("--update", help="Update isin database", action="store_true")
    args = parser.parse_args()
    if args.version:
        print_version()
    elif args.update:
        update_isin_db()
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
