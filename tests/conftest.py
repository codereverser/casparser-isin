import argparse
import shutil

import pytest

from casparser_isin.utils import INTERNAL_ISIN_DB_PATH


@pytest.fixture
def isolated_isin_db(tmp_path, monkeypatch):
    """
    Copy the bundled ISIN database into a temp dir and point
    ``CASPARSER_ISIN_DB`` at the copy.

    Tests that modify the DB (download / overwrite) use this fixture so the
    real bundled database is never touched.
    """
    target = tmp_path / "isin.db"
    shutil.copy2(INTERNAL_ISIN_DB_PATH, target)
    monkeypatch.setenv("CASPARSER_ISIN_DB", str(target))
    return target


@pytest.fixture
def version_cli(monkeypatch):
    monkeypatch.setattr(
        argparse.ArgumentParser,
        "parse_args",
        lambda x: argparse.Namespace(update=False, version=True, check=False),
    )


@pytest.fixture
def check_cli(monkeypatch):
    monkeypatch.setattr(
        argparse.ArgumentParser,
        "parse_args",
        lambda x: argparse.Namespace(update=False, version=False, check=True),
    )


@pytest.fixture
def help_cli(monkeypatch):
    monkeypatch.setattr(
        argparse.ArgumentParser,
        "parse_args",
        lambda x: argparse.Namespace(update=False, version=False, check=False),
    )


@pytest.fixture
def update_cli(monkeypatch):
    monkeypatch.setattr(
        argparse.ArgumentParser,
        "parse_args",
        lambda x: argparse.Namespace(update=True, version=False, check=False),
    )
