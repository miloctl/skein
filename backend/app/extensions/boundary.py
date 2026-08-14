"""Refuse a private extension package that imports Skein internals.

A package that reaches past the published contracts keeps working until a
compatible core release changes an internal shape, and then fails in a
deployment instead of in a test run. This check is what turns that silent
drift into a failure the private repository sees first.

The check is static and reads source only. It is not a security boundary: an
in-process module is trusted code with the same operating-system permissions
as Skein, and a dynamic import evades this entirely.
"""

from __future__ import annotations

import ast
import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

from .registry import ExtensionValidationError

# docs/EXTENSIONS.md names these three as the whole backend contract. Every
# other module under `app.` is internal, so an import of one is the finding.
# A submodule of a public package counts as internal too: `app.public` exports
# what a package is allowed to hold, and reaching around that export list is
# the same drift with a shorter path.
_PUBLIC_MODULES = frozenset({"app.extensions", "app.main", "app.public"})


def _roots(package: ModuleType | str | Path) -> tuple[Path, ...]:
    if isinstance(package, Path):
        return (package,)
    if isinstance(package, str):
        spec = importlib.util.find_spec(package)
        if spec is None:
            raise ExtensionValidationError(f"package {package!r} is not importable")
        locations = tuple(spec.submodule_search_locations or ())
        origin = spec.origin
    else:
        locations = tuple(getattr(package, "__path__", ()) or ())
        origin = getattr(package, "__file__", None)
    if locations:
        return tuple(Path(item) for item in locations)
    if origin:
        return (Path(origin),)
    raise ExtensionValidationError("the package has no source to check")


def _source_files(root: Path) -> Iterator[Path]:
    if root.is_file():
        yield root
        return
    yield from sorted(root.rglob("*.py"))


def _imported_modules(tree: ast.Module) -> Iterator[tuple[int, str]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            # A relative import stays inside the private package's own tree and
            # can never name `app.`.
            if node.level or not node.module:
                continue
            yield node.lineno, node.module
            if node.module == "app":
                # `from app import services` carries the module name in the
                # alias, so checking node.module alone would pass it.
                for alias in node.names:
                    yield node.lineno, f"app.{alias.name}"


def _is_internal(name: str) -> bool:
    if name != "app" and not name.startswith("app."):
        return False
    return name not in _PUBLIC_MODULES


def assert_import_boundary(package: ModuleType | str | Path) -> None:
    """Raise when a package imports a Skein module outside the public contract.

    Accepts an imported package, a dotted package name, or a path to a file or
    directory of source. `docs/EXTENSIONS.md` requires this check in a private
    extension repository.
    """
    violations: list[str] = []
    for root in _roots(package):
        for path in _source_files(root):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError) as exc:
                raise ExtensionValidationError(f"{path} is not readable Python source") from exc
            for lineno, name in _imported_modules(tree):
                if _is_internal(name):
                    try:
                        label = path.relative_to(root.parent)
                    except ValueError:
                        label = path
                    violations.append(f"{label}:{lineno} imports {name}")
    if violations:
        allowed = ", ".join(sorted(_PUBLIC_MODULES))
        raise ExtensionValidationError(
            "this package imports Skein internal modules:\n  "
            + "\n  ".join(sorted(violations))
            + f"\nImport backend contracts only from {allowed}."
        )
