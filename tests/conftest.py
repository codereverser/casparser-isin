import argparse
import builtins
from io import BytesIO, StringIO
import os

import pytest


class MockFileManager:
    def __init__(self):
        self.files = {}
        self._open = builtins.open

    def open(self, name, mode="r", buffering=-1, **options):
        name = os.path.abspath(name)
        if mode.startswith("r") and name not in self.files:
            # We have to let some files through
            return self._open(name, mode, buffering, **options)
            # This causes stack traces not to display
            # raise IOError(2, "No such file or directory: '%s'" % name)

        if mode.startswith("w") or (mode.startswith("a") and name not in self.files):
            if "b" in mode:
                buf = BytesIO()
            else:
                buf = StringIO()
                buf.close = lambda: None
            self.files[name] = buf

        buf = self.files[name]

        if mode.startswith("r"):
            buf.seek(0)
        elif mode.startswith("a"):
            buf.seek(0)
        return buf

    def write(self, name, text):
        name = os.path.abspath(name)
        buf = StringIO(text)
        buf.close = lambda: None
        self.files[name] = buf

    def read(self, name):
        name = os.path.abspath(name)
        if name not in self.files:
            raise IOError(2, "No such file or directory: '%s'" % name)

        return self.files[name].getvalue()


@pytest.fixture
def mockopen(monkeypatch):
    manager = MockFileManager()
    monkeypatch.setattr(builtins, "open", manager.open)
    return manager


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
