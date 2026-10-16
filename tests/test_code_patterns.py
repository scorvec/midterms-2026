"""Rewrites a linter considers equivalent that are not."""

import ast
from pathlib import Path

import midterms

PKG = Path(midterms.__file__).resolve().parent
GROUPERS = {"groupby", "resample", "rolling", "expanding", "ewm"}


def dict_over_grouper(source: str) -> list[int]:
    """Lines of `dict(<expr>.groupby(...))` and the like. A pandas GroupBy has a `.keys` attribute (the grouping key), so
    dict() treats it as a mapping and calls it - "TypeError: 'str' object is not callable" - instead of iterating the
    (key, group) pairs that `{k: g for k, g in df.groupby(...)}` iterates. ruff's C416 fix makes exactly this rewrite."""
    return [
        n.lineno
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "dict"
        and len(n.args) == 1
        and isinstance(n.args[0], ast.Call)
        and isinstance(n.args[0].func, ast.Attribute)
        and n.args[0].func.attr in GROUPERS
    ]


def test_guard_flags_the_rewrite():
    assert dict_over_grouper('idx = dict(T.groupby("key"))\nok = {k: g for k, g in T.groupby("key")}\n') == [1]


def test_no_dict_over_groupby():
    bad = [
        f"{path.relative_to(PKG.parent)}:{line}"
        for path in sorted(PKG.rglob("*.py"))
        for line in dict_over_grouper(path.read_text(encoding="utf-8"))
    ]
    assert not bad, "dict() over a pandas grouper (keep the comprehension, with noqa: C416): " + ", ".join(bad)
