"""Shared fixtures and import-skip wiring for tools tests.

The build pipeline lives under ``tools/cptools`` and depends on the
``tools`` dependency group (lxml, b2sdk, requests-cache). When that group
isn't installed (e.g. running ``uv sync`` without ``--group tools``), this
whole subtree is skipped.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# tools/ isn't a proper installable package, so make its modules importable
# the same way the orchestrator script does -- by injecting tools/ onto sys.path.
_TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))


# Skip the whole subtree if the tools group hasn't been installed.
pytest.importorskip("lxml")
pytest.importorskip("requests")
