"""Regression guard for issue #283: an unparented QDialog is a fully
independent top-level window to macOS, so opening one while the main
window is in native full-screen kicks the app to a new desktop/Space
(issue #15's original bug, fixed for ConnectionDialog at main.py's
_prompt_new_connection by parenting it to `self`).

Manually auditing every `QDialog(` constructor call and every QDialog
subclass under ui/ (2026-09-13) found all of them already correctly
parented — up to a real widget, never a bare/omitted/None parent. This
test locks that in with a static AST scan so a future dialog added
without parenting fails the suite instead of silently reintroducing #15's
bug, rather than re-auditing by hand every time.
"""
import ast
import glob
import os

_UI_DIR = os.path.join(os.path.dirname(__file__), "..", "ui")


def _parse(path):
    with open(path) as f:
        return ast.parse(f.read(), filename=path)


def _iter_calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def _iter_dialog_subclasses(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(
            isinstance(b, ast.Name) and b.id == "QDialog" for b in node.bases
        ):
            yield node


def test_every_raw_qdialog_construction_passes_a_non_none_parent():
    violations = []
    for path in glob.glob(os.path.join(_UI_DIR, "*.py")):
        tree = _parse(path)
        for call in _iter_calls(tree):
            if isinstance(call.func, ast.Name) and call.func.id == "QDialog":
                bad = (
                    len(call.args) == 0
                    or (isinstance(call.args[0], ast.Constant) and call.args[0].value is None)
                )
                if bad:
                    violations.append(f"{os.path.basename(path)}:{call.lineno}")
    assert violations == [], (
        f"QDialog(...) constructed with no/None parent — an unparented "
        f"dialog reproduces issue #15 in native fullscreen: {violations}"
    )


def test_every_qdialog_subclass_forwards_parent_to_super_init():
    violations = []
    for path in glob.glob(os.path.join(_UI_DIR, "*.py")):
        tree = _parse(path)
        for cls in _iter_dialog_subclasses(tree):
            init = next(
                (n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"),
                None,
            )
            if init is None:
                continue  # inherits __init__ from QDialog itself — parent forwarding is Qt's job
            accepts_parent = any(a.arg == "parent" for a in init.args.args + init.args.kwonlyargs)
            if not accepts_parent:
                continue  # this dialog deliberately has no parent param — not this test's concern
            super_calls = [
                n for n in ast.walk(init)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "__init__"
                and isinstance(n.func.value, ast.Call)
                and isinstance(n.func.value.func, ast.Name) and n.func.value.func.id == "super"
            ]
            if not super_calls or all(len(c.args) == 0 for c in super_calls):
                violations.append(f"{os.path.basename(path)}:{cls.name}")
    assert violations == [], (
        f"QDialog subclass accepts `parent` but never forwards it to "
        f"super().__init__(parent) — the parent argument is silently "
        f"dropped, reproducing issue #15: {violations}"
    )
