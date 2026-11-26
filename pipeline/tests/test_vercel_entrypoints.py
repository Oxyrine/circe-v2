"""Vercel picks a function's entrypoint from its top-level names; get them wrong and it 500s."""
import ast
from pathlib import Path

import pytest

FILES = sorted((Path(__file__).resolve().parents[2] / "api").glob("*.py"))


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_only_a_literal_handler_class_is_exported(path):
    body = ast.parse(path.read_text(encoding="utf-8")).body
    assert any(isinstance(n, ast.ClassDef) and n.name == "handler" for n in body)
    assigned = {t.id for n in body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    assert not assigned & {"app", "application"}
