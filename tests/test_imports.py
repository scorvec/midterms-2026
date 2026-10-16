"""Every module of the package loads from a fresh clone: syntax errors and import-time failures surface here, not in the
daily run."""

import importlib
import pkgutil
from pathlib import Path

import pytest

import midterms

PKG = Path(midterms.__file__).resolve().parent
RUN_ON_IMPORT = {"refresh"}  # scripts that do their work when imported (python -m midterms.refresh): compiled only
MODULES = sorted(m.name for m in pkgutil.iter_modules([str(PKG)]))


@pytest.mark.parametrize("name", [m for m in MODULES if m not in RUN_ON_IMPORT])
def test_imports(name):
    importlib.import_module(f"midterms.{name}")


@pytest.mark.parametrize("name", sorted(RUN_ON_IMPORT))
def test_compiles(name):
    path = PKG / f"{name}.py"
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
