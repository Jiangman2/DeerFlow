import ast
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def composer(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    spec = importlib.util.spec_from_file_location("employee_gateway_delta", Path(__file__).with_name("gateway_delta.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_selective_symbols_preserve_unrelated_production_code(composer):
    source = '"""prod"""\nfrom __future__ import annotations\n\nGUARD = 1\ndef chosen():\n    return "old"\n\ndef streaming():\n    return "production"\n'
    candidate = 'GUARD = 2\nNEW = 3\ndef chosen():\n    return NEW\n\ndef streaming():\n    return broken_new_variable\n'
    result = composer.select_symbols(source, candidate, ("chosen", "NEW"), ("from math import pi",))
    assert 'return "production"' in result and "broken_new_variable" not in result
    assert "GUARD = 1" in result and "NEW = 3" in result
    assert result.index("from __future__") < result.index("from math import pi")
    ast.parse(result)


def test_ambiguous_or_missing_symbols_are_not_fuzzy_patched(composer):
    with pytest.raises(ValueError):
        composer.select_symbols("A=1\nA=2\n", "A=3\n", ("A",), ())
    with pytest.raises(ValueError):
        composer.select_symbols("A=1\n", "B=2\n", ("A",), ())


def test_actual_employee_recipe_keeps_production_sse_and_other_thread_handlers(composer):
    root = Path(__file__).resolve().parents[2]
    baseline = Path(__file__).with_name("audited-baseline-20260924-supplement") / "gateway"
    if not baseline.is_dir():
        pytest.skip("requires supplied and checksum-verified production source audit")
    for relative in composer.RECIPES:
        source = (baseline / relative).read_text(encoding="utf-8")
        candidate = (root / "backend" / relative).read_text(encoding="utf-8")
        result, manifest = composer.compose(relative, source.encode(), candidate.encode())
        selected = set(composer.RECIPES[relative]["symbols"]) | set(composer.RECIPES[relative].get("adapted_symbols", ()))
        inserted = {item["symbol"] for item in composer.RECIPES[relative].get("insertions", ())}
        old = composer.symbols(source)
        new = composer.symbols(result.decode())
        for name, text in old.items():
            if name not in selected | inserted:
                assert new[name] == text, name
        for insertion in composer.RECIPES[relative].get("insertions", ()):
            name = insertion["symbol"]
            assert new[name].replace(insertion["anchor"] + insertion["addition"], insertion["anchor"], 1) == old[name]
        if relative.endswith("services.py"):
            assert "gap_emitted" not in result.decode()
        assert manifest["source_sha256"] != manifest["target_sha256"]


def test_employee_storage_and_config_are_explicit_recipes(composer):
    expected = {
        "packages/harness/deerflow/config/agents_config.py",
        "packages/harness/deerflow/persistence/agents/base.py",
        "packages/harness/deerflow/persistence/agents/file.py",
        "packages/harness/deerflow/persistence/agents/sql.py",
    }
    assert expected <= composer.RECIPES.keys()


def test_new_type_alias_precedes_eager_annotations_without_future_import(composer):
    source = "from typing import Literal\ndef chosen(value: str):\n    return value\n\ndef preserved():\n    return 'original'\n"
    candidate = "from typing import Literal\nSource = Literal['personal', 'team']\ndef validate(value):\n    return value\ndef chosen(value: Source):\n    return validate(value)\n\ndef preserved():\n    return 'unrelated-change'\n"
    result = composer.select_symbols(source, candidate, ("chosen", "validate", "Source"), ())
    namespace = {}
    exec(result, namespace)
    assert namespace["chosen"]("team") == "team"
    assert namespace["preserved"]() == "original"
    assert result.index("Source =") < result.index("def chosen")


def test_employee_routes_are_composed_but_other_startup_changes_are_not(composer):
    relative = "app/gateway/app.py"
    assert relative in composer.RECIPES
    root = Path(__file__).resolve().parents[2]
    baseline = Path(__file__).with_name("audited-baseline-20260924") / "gateway"
    if not baseline.is_dir():
        pytest.skip("requires supplied production source audit")
    result, manifest = composer.compose(relative, (baseline / relative).read_bytes(), (root / "backend" / relative).read_bytes())
    text = result.decode()
    assert "register_employee_resource_services(search=employee_broker.search" in text
    assert "app.include_router(employee_changes.router)" in text
    assert "app.include_router(employee_resources.router)" in text
    assert "migrated_categories =" not in text
    assert "shutdown_memory_backend" not in text
    assert "manager.shutdown_flush" in text
    assert manifest["anchored_insertions"] == ["lifespan", "create_app"]


def test_anchored_insertion_rejects_changed_or_repeated_anchor(composer):
    source = "def boot():\n    ready()\n    keep()\n"
    candidate = "def boot():\n    ready()\n    employee()\n    unrelated()\n"
    insertion = {"symbol": "boot", "anchor": "    ready()\n", "addition": "    employee()\n"}
    result = composer.insert_reviewed(source, candidate, (insertion,))
    assert result == "def boot():\n    ready()\n    employee()\n    keep()\n"
    for bad in (source.replace("ready()", "changed()"), source.replace("    keep()\n", "    ready()\n")):
        with pytest.raises(ValueError, match="anchor"):
            composer.insert_reviewed(bad, candidate, (insertion,))
    with pytest.raises(ValueError, match="candidate"):
        composer.insert_reviewed(source, candidate.replace("employee()", "different()"), (insertion,))
