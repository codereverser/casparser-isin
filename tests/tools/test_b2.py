"""Unit tests for the testable pieces of the B2 upload (no network / no creds)."""

from __future__ import annotations

import gzip

from cptools.b2 import _gzip_to_temp


def test_gzip_to_temp_round_trips(tmp_path):
    src = tmp_path / "isin.db"
    payload = b"sqlite-format-3\x00" + b"x" * 10_000  # compressible blob
    src.write_bytes(payload)

    gz = _gzip_to_temp(src)
    try:
        assert gz.suffix == ".gz"
        # The client decompresses with gzip — round-trip must match exactly.
        assert gzip.decompress(gz.read_bytes()) == payload
        # Compressible payload should actually shrink.
        assert gz.stat().st_size < len(payload)
    finally:
        gz.unlink(missing_ok=True)
