import argparse
import logging
import pathlib
import sqlite3
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .__version__ import __version__

BASE_DIR = pathlib.Path(__file__).resolve().parent
ISIN_DB_PATH = BASE_DIR / "isin.db"

META_URL = "https://casparser.atomcoder.com/isin.db.meta"
DB_URL = "https://casparser.atomcoder.com/isin.db"


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


def build_request(url):
    hdr = {
        "User Agent": f"casparser-isin {__version__}",
        "X-origin-casparser": "true",
    }
    return Request(url, headers=hdr)


def update_isin_db():
    local_meta = get_metadata()
    logging.info("Fetching remote isin db metadata")
    try:
        response = urlopen(build_request(META_URL))
    except HTTPError as e:
        logging.error("Received error from remote server :: %s", e.read().decode())
        return
    data = response.read().decode()
    remote_meta = {}
    for line in data.splitlines():
        split = line.split("=")
        if len(split) == 2:
            k, v = split
            remote_meta[k.strip()] = v.strip()
    logging.info("Local db version  : %s", local_meta.get("version"))
    logging.info("Remote db version : %s", remote_meta.get("version"))
    if (
        remote_meta["version"] > local_meta["version"]
        and remote_meta["dbformat"] == local_meta["dbformat"]
    ):
        logging.info("Fetching database files")
        response = urlopen(build_request(DB_URL))
        data = response.read()
        with open(ISIN_DB_PATH, "wb") as f:
            f.write(data)
        logging.info("Updated casparser-isin database.")
    else:
        logging.info("casparser-isin database is already upto date")


def main():
    parser = argparse.ArgumentParser("casparser-isin", description="casparser-isin cli")
    parser.add_argument("-v", "--version", help="Print version information", action="store_true")
    parser.add_argument("--update", help="Update isin database", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    if args.version:
        print_version()
    elif args.update:
        update_isin_db()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
