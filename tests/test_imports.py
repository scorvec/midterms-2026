"""Every module of the package loads from a fresh clone: syntax errors and import-time failures surface here, not in the
daily run."""

import importlib
import pkgutil
from pathlib import Path

import pytest

import midterms

PKG = Path(midterms.__file__).resolve().parent
# compiled only: modules that do their work when imported, and the district-map build tools, whose workflow
# (stateleg-geo.yml) installs the geo stack (geopandas, pyproj) that the forecast itself does not need
COMPILE_ONLY = {
    "refresh": "runs the refresh when imported (python -m midterms.refresh)",
    "district_shapes": "map build tool; needs geopandas",
    "urban": "map build tool; needs pyproj",
}
MODULES = sorted(m.name for m in pkgutil.iter_modules([str(PKG)]))


@pytest.mark.parametrize("name", [m for m in MODULES if m not in COMPILE_ONLY])
def test_imports(name):
    importlib.import_module(f"midterms.{name}")


@pytest.mark.parametrize("name", sorted(COMPILE_ONLY))
def test_compiles(name):
    path = PKG / f"{name}.py"
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
