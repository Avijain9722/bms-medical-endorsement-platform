"""Regression tests for the offline Windows bundle gate."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

CHECKER_PATH = Path(__file__).resolve().parents[1] / "tools" / "check_wheelhouse.py"
SPEC = importlib.util.spec_from_file_location("check_wheelhouse", CHECKER_PATH)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


@pytest.mark.parametrize(
    "filename",
    [
        "ruff-0.16.1-py3-none-win_amd64.whl",
        "greenlet-3.5.4-cp314-cp314-win_amd64.whl",
        "example-1.0-cp38-abi3-win_amd64.whl",
        "example-1.0-py3-none-any.whl",
    ],
)
def test_python_314_accepts_compatible_windows_wheel_tags(filename):
    assert checker.wheel_supports_target(Path(filename), "3.14", "win_amd64")


@pytest.mark.parametrize(
    "filename",
    [
        "greenlet-3.5.4-cp313-cp313-win_amd64.whl",
        "greenlet-3.5.4-cp314-cp314-win32.whl",
        "invalid.whl",
    ],
)
def test_python_314_rejects_incompatible_wheel_tags(filename):
    assert not checker.wheel_supports_target(Path(filename), "3.14", "win_amd64")
