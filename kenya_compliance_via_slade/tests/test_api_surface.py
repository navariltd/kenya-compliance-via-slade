"""Surface tests: every whitelisted API function exists and is registered."""

from __future__ import annotations

import ast
import importlib
import pathlib

import frappe
from frappe.tests import UnitTestCase

#: Directory holding the app's Python source (the inner package).
APP_SOURCE_ROOT = pathlib.Path(__file__).resolve().parents[1] / "kenya_compliance_via_slade"


def _module_name(path: pathlib.Path) -> str:
    """Return the dotted import path for a source file."""
    relative = path.relative_to(APP_SOURCE_ROOT).with_suffix("")
    return "kenya_compliance_via_slade.kenya_compliance_via_slade." + ".".join(relative.parts)


def _is_whitelist_decorator(node: ast.AST) -> bool:
    """Return whether *node* is a ``frappe.whitelist`` decorator."""
    if isinstance(node, ast.Call):
        return getattr(node.func, "attr", "") == "whitelist"
    return isinstance(node, ast.Attribute) and node.attr == "whitelist"


def _discover_whitelisted_functions() -> list[tuple[str, str]]:
    """Return ``(module_path, function_name)`` for every whitelisted API."""
    discovered: list[tuple[str, str]] = []

    for path in sorted(APP_SOURCE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue

        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue

        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if any(_is_whitelist_decorator(d) for d in node.decorator_list):
                discovered.append((_module_name(path), node.name))

    return discovered


class TestApiSurface(UnitTestCase):
    """All whitelisted API functions are importable and registered."""

    def test_all_whitelisted_functions_are_discoverable(self) -> None:
        """The app exposes a non-trivial number of whitelisted endpoints."""
        self.assertGreater(len(_discover_whitelisted_functions()), 50)

    def test_every_whitelisted_function_is_importable_and_registered(self) -> None:
        """Each discovered API imports cleanly and is registered whitelisted."""
        for module_path, function_name in _discover_whitelisted_functions():
            with self.subTest(api=f"{module_path}.{function_name}"):
                module = importlib.import_module(module_path)
                function = getattr(module, function_name)

                self.assertTrue(callable(function))
                self.assertIn(
                    function,
                    frappe.whitelisted,
                    f"{module_path}.{function_name} is not whitelisted",
                )
