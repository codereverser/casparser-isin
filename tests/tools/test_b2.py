"""Unit tests for the testable pieces of the B2 upload (no network / no creds)."""

from __future__ import annotations

import gzip

from b2sdk.v2 import AbstractProgressListener
from cptools.b2 import _gzip_to_temp, _LoggingProgressListener, _progress_listener


def test_progress_listener_non_tty_routes_to_logger(monkeypatch):
    # Under cron / piped output progress goes through the logger, not a tqdm
    # bar that repaints with carriage returns.
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)
    listener = _progress_listener("isin.db")
    assert isinstance(listener, _LoggingProgressListener)


def test_progress_listener_tty_returns_a_listener(monkeypatch):
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)
    listener = _progress_listener("isin.db")
    # tqdm may or may not be installed; either way it's a valid listener.
    assert isinstance(listener, AbstractProgressListener)


def test_logging_progress_listener_logs_throttled_and_final(caplog):
    import logging

    listener = _LoggingProgressListener("isin.db")
    listener.set_total_bytes(1000)
    with caplog.at_level(logging.INFO, logger="cptools"):
        listener.bytes_completed(100)  # first tick (last_log=0) -> logs
        listener.bytes_completed(200)  # within 5s, not final -> throttled
        listener.bytes_completed(1000)  # final -> always logs
    pct = [r.getMessage() for r in caplog.records]
    assert any("10%" in m for m in pct)
    assert any("100%" in m for m in pct)
    assert not any("20%" in m for m in pct)  # throttled out


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
