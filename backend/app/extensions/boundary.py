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
    yield from sorted(path for path in root.rglob("*") if path.suffix in {".py", ".pyi"})


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
            if node.module == "app.main":
                # create_app is the one symbol docs/EXTENSIONS.md publishes
                # from this module. A module import or any other alias reaches
                # private startup machinery that can change in a patch.
                for alias in node.names:
                    if alias.name != "create_app":
                        yield node.lineno, f"app.main.{alias.name}"
                continue
            yield node.lineno, node.module
            if node.module == "app" or node.module in _PUBLIC_MODULES:
                # `from app import services` carries the module name in the
                # alias, and so does `from app.public import events`. Checking
                # node.module alone passes both. An alias that names an
                # exported symbol rather than a submodule is not a real module
                # path, so _is_internal only fires on one that resolves.
                for alias in node.names:
                    yield node.lineno, f"{node.module}.{alias.name}"


def _is_internal(name: str) -> bool:
    if name != "app" and not name.startswith("app."):
        return False
    if name == "app.main" or name.startswith("app.main."):
        return True
    if name in _PUBLIC_MODULES:
        return False
    if name.count(".") > 1 and name.rsplit(".", 1)[0] in _PUBLIC_MODULES:
        # `from app.public import CreateTaskCommand` names an exported symbol,
        # not a module. Only flag the alias when it resolves to a real module.
        # find_spec raises rather than returning None when the parent is a
        # module and not a package, which is every `app.main.<symbol>`.
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, AttributeError, ValueError):
            return False
    return True


def assert_import_boundary(package: ModuleType | str | Path) -> None:
    """Raise when a package imports a Skein module outside the public contract.

    Accepts an imported package, a dotted package name, or a path to a file or
    directory of source. `docs/EXTENSIONS.md` requires this check in a private
    extension repository.
    """
    violations: list[str] = []
    read = 0
    for root in _roots(package):
        for path in _source_files(root):
            read += 1
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
    if not read:
        # A zipped or byte-compiled install yields no source, and reporting
        # that as a pass hands the caller a green check that read nothing.
        raise ExtensionValidationError(
            "this package has no Python source to check."
            " Point the check at the source tree, not an installed archive."
        )
    if violations:
        allowed = ", ".join(("app.extensions", "app.main.create_app", "app.public"))
        raise ExtensionValidationError(
            "this package imports Skein internal modules:\n  "
            + "\n  ".join(sorted(violations))
            + f"\nImport backend contracts only from {allowed}."
        )
