import argparse
import logging
from packaging import version
import sqlite3
import sys
from urllib.error import HTTPError
from urllib import request

from . import __version__
from .utils import get_isin_db_path

META_URL = "https://casparser.atomcoder.com/isin.db.meta"
DB_URL = "https://casparser.atomcoder.com/isin.db"


def get_metadata():
    conn = sqlite3.connect(get_isin_db_path())
    cursor = conn.cursor()
    try:
        with conn:
            cursor.execute("SELECT key, value from meta")
            metadata = {}
            for key, value in cursor.fetchall():
                if key in ("dbformat", "version"):
                    value = version.parse(value)
                metadata[key] = value
            metadata["cli-version"] = version.parse(__version__)
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
    return request.Request(url, headers=hdr)


def get_isin_db_details():
    local_meta = get_metadata()
    remote_meta = None
    logging.info("Fetching remote isin db metadata")
    try:
        with request.urlopen(build_request(META_URL)) as response:
            data = response.read().decode()
    except HTTPError as e:
        logging.error("Received error from remote server :: %s", e.reason)
    else:
        remote_meta = {}
        for line in data.splitlines():
            split = line.split("=")
            if len(split) == 2:
                key, value = [x.strip() for x in split]
                if key in ("dbformat", "version"):
                    value = version.parse(value)
                remote_meta[key] = value
        logging.info("Local db version  : %s", local_meta.get("version"))
        logging.info("Remote db version : %s", remote_meta.get("version"))
    return remote_meta, local_meta


def check_isin_db():
    """Compare remote and local db versions
    Return code:
    0 - no new database available
    1 - new database available
    """
    remote_meta, local_meta = get_isin_db_details()
    if (
        remote_meta is not None
        and remote_meta["version"] > local_meta["version"]
        and remote_meta["dbformat"] == local_meta["dbformat"]
    ):
        logging.info("To update the database, re-run the command with --update flag.")
        sys.exit(1)
    else:
        logging.info("Local database is up to date.")
        sys.exit(0)


def update_isin_db():
    remote_meta, local_meta = get_isin_db_details()
    if remote_meta is None:
        return
    elif (
        remote_meta["version"] > local_meta["version"]
        and remote_meta["dbformat"] == local_meta["dbformat"]
    ):
        logging.info("Fetching database version :: %s", remote_meta["version"])
        try:
            with request.urlopen(build_request(DB_URL)) as response:
                data = response.read()
        except HTTPError as e:
            logging.error("Error fetching isin database :: %s", e.reason)
            return
        with open(get_isin_db_path(), "wb") as f:
            f.write(data)
        logging.info("Updated casparser-isin database.")
    else:
        logging.info("casparser-isin database is already upto date")


def main():
    parser = argparse.ArgumentParser("casparser-isin", description="casparser-isin cli")
    parser.add_argument("-v", "--version", help="Print version information", action="store_true")
    parser.add_argument("--update", help="Update isin database", action="store_true")
    parser.add_argument("--check", help="Check remote isin database version", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    if args.version:
        print_version()
    elif args.update:
        update_isin_db()
    elif args.check:
        check_isin_db()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
