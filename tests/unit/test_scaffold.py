"""The package layout from CLAUDE.md exists and every package states its purpose."""

import importlib

import pytest

PACKAGES = [
    "pg_core",
    "pg_sdk",
    "pg_generator",
    "pg_runner",
    "pg_agent",
    "pg_api",
    "pg_worker",
    "pg_dashboard",
    "pg_cli",
]


@pytest.mark.parametrize("name", PACKAGES)
def test_package_imports_and_has_docstring(name: str) -> None:
    module = importlib.import_module(name)
    assert module.__doc__ is not None
    assert module.__doc__.strip()
