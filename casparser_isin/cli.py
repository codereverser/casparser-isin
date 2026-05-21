import argparse
import hashlib
import logging
import os
import sqlite3
import sys
import tempfile
from urllib import request
from urllib.error import HTTPError

from packaging import version

from . import __version__
from .utils import get_isin_db_path

META_URL = "https://casparser.atomcoder.com/isin.db.meta"
DB_URL = "https://casparser.atomcoder.com/isin.db"

# Stream the DB download in 1 MiB chunks. The DB is ~50 MB; this caps peak
# memory at ~1 MiB regardless of file size and lets shutil.copyfileobj move
# bytes straight from socket to disk.
_DOWNLOAD_CHUNK = 1024 * 1024


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
        "User-Agent": f"casparser-isin/{__version__}",
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


def _download_to_temp(url: str, dest_dir, *, expected_sha256: str | None = None):
    """
    Stream ``url`` into a temp file alongside ``dest_dir`` and return its path.

    Streaming avoids holding the entire ~50 MB DB in memory. Writing to a temp
    file in the same directory as the final destination guarantees the
    subsequent ``os.replace`` is atomic (same filesystem). If
    ``expected_sha256`` is supplied, the download is verified and the temp file
    deleted on mismatch.
    """
    fd, tmp_path = tempfile.mkstemp(prefix="isin.db.", suffix=".tmp", dir=str(dest_dir))
    sha = hashlib.sha256()
    try:
        with request.urlopen(build_request(url)) as response, os.fdopen(fd, "wb") as out:
            while True:
                chunk = response.read(_DOWNLOAD_CHUNK)
                if not chunk:
                    break
                sha.update(chunk)
                out.write(chunk)
        if expected_sha256 is not None:
            actual = sha.hexdigest()
            if actual.lower() != expected_sha256.lower():
                raise ValueError(f"SHA256 mismatch: expected {expected_sha256}, got {actual}")
    except BaseException:
        # Make sure we don't leave half-written temp files behind on any failure
        # path (including KeyboardInterrupt).
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise
    return tmp_path


def update_isin_db():
    remote_meta, local_meta = get_isin_db_details()
    if remote_meta is None:
        return
    elif (
        remote_meta["version"] > local_meta["version"]
        and remote_meta["dbformat"] == local_meta["dbformat"]
    ):
        logging.info("Fetching database version :: %s", remote_meta["version"])
        dest = get_isin_db_path()
        dest_dir = dest.parent
        try:
            tmp_path = _download_to_temp(
                DB_URL,
                dest_dir,
                expected_sha256=remote_meta.get("sha256"),
            )
        except HTTPError as e:
            logging.error("Error fetching isin database :: %s", e.reason)
            return
        except ValueError as e:
            logging.error("Database integrity check failed :: %s", e)
            return
        except OSError as e:
            logging.error("Error writing isin database :: %s", e)
            return
        # Atomic swap: os.replace is atomic when source and destination are on
        # the same filesystem (guaranteed because the temp file was created in
        # dest_dir).
        os.replace(tmp_path, dest)
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
