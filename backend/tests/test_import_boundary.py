"""What the published import wall accepts and refuses."""

from pathlib import Path

import pytest

from app.extensions import ExtensionValidationError, assert_import_boundary

ROOT = Path(__file__).resolve().parents[2]
ATLAS = ROOT / "examples/workplace-extension/backend/src/atlas_skein"


def _package(tmp_path: Path, source: str) -> Path:
    package = tmp_path / "private_package"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "adapter.py").write_text(source)
    return package


def test_the_reference_extension_holds_the_boundary():
    """Atlas is the worked example the authoring guide points at. An internal
    import here would teach every private package the wrong pattern."""
    assert_import_boundary(ATLAS)


def test_a_public_contract_import_is_accepted(tmp_path):
    assert_import_boundary(
        _package(
            tmp_path,
            "from app.extensions import SkeinModule\n"
            "from app.main import create_app\n"
            "from app.public import CreateTaskCommand\n",
        )
    )


@pytest.mark.parametrize(
    "source",
    [
        "import app.main\n",
        "from app.main import *\n",
        "from app.main import _job_specs\n",
    ],
)
def test_app_main_exposes_only_create_app(tmp_path, source):
    package = _package(tmp_path, source)

    with pytest.raises(ExtensionValidationError) as exc:
        assert_import_boundary(package)

    assert "app.main" in str(exc.value)


def test_an_internal_import_names_its_file_and_line(tmp_path):
    package = _package(tmp_path, "import os\n\nfrom app.services import work\n")

    with pytest.raises(ExtensionValidationError) as exc:
        assert_import_boundary(package)

    assert "private_package/adapter.py:3 imports app.services" in str(exc.value)
    assert "app.extensions, app.main.create_app, app.public" in str(exc.value)


def test_a_module_named_in_the_alias_is_refused(tmp_path):
    """`from app import services` carries the module in the alias, so a check
    that reads the imported-from module alone passes it."""
    package = _package(tmp_path, "from app import services\n")

    with pytest.raises(ExtensionValidationError) as exc:
        assert_import_boundary(package)

    assert "imports app.services" in str(exc.value)


def test_reaching_around_a_public_export_list_is_refused(tmp_path):
    """A submodule of a public package is internal: the export list is the
    contract, and a deep import keeps working until the module moves."""
    package = _package(tmp_path, "from app.extensions.contracts import SkeinModule\n")

    with pytest.raises(ExtensionValidationError) as exc:
        assert_import_boundary(package)

    assert "imports app.extensions.contracts" in str(exc.value)


def test_a_bare_core_import_is_refused(tmp_path):
    package = _package(tmp_path, "import app\n")

    with pytest.raises(ExtensionValidationError) as exc:
        assert_import_boundary(package)

    assert "imports app" in str(exc.value)


def test_a_relative_import_is_not_a_core_import(tmp_path):
    """A private package's own `from .app import thing` names its own module."""
    package = _package(tmp_path, "from .app import helper\nfrom . import sibling\n")

    assert_import_boundary(package)


def test_every_source_file_is_read(tmp_path):
    package = _package(tmp_path, "from app.public import TaskView\n")
    (package / "nested").mkdir()
    (package / "nested" / "sync.py").write_text("from app.db import query\n")

    with pytest.raises(ExtensionValidationError) as exc:
        assert_import_boundary(package)

    assert "nested/sync.py:1 imports app.db" in str(exc.value)


def test_a_dotted_package_name_resolves_to_its_source(tmp_path, monkeypatch):
    _package(tmp_path, "from app.routes import deps\n")
    monkeypatch.syspath_prepend(str(tmp_path))

    with pytest.raises(ExtensionValidationError) as exc:
        assert_import_boundary("private_package")

    assert "imports app.routes" in str(exc.value)


def test_an_unimportable_name_is_reported_as_such():
    with pytest.raises(ExtensionValidationError) as exc:
        assert_import_boundary("skein_package_that_does_not_exist")

    assert "is not importable" in str(exc.value)


def test_a_submodule_of_a_public_package_is_refused(tmp_path):
    """`from app.public import events` reaches around the export list the same
    way the dotted form does. Checking the imported-from module alone passes
    it, because the module IS public and the submodule hides in the alias."""
    package = _package(tmp_path, "from app.public import events\n")

    with pytest.raises(ExtensionValidationError) as exc:
        assert_import_boundary(package)

    assert "imports app.public.events" in str(exc.value)


def test_an_exported_symbol_is_not_mistaken_for_a_submodule(tmp_path):
    """The alias in `from app.public import CreateTaskCommand` names a symbol,
    not a module, so widening the check must not refuse the documented import."""
    assert_import_boundary(
        _package(
            tmp_path,
            "from app.public import CreateTaskCommand, WorkItems\n"
            "from app.extensions import SkeinModule\n"
            "from app.main import create_app\n",
        )
    )


def test_a_stub_file_cannot_import_an_internal_module(tmp_path):
    package = _package(tmp_path, "")
    (package / "adapter.pyi").write_text("from app.services import work\n")

    with pytest.raises(ExtensionValidationError) as exc:
        assert_import_boundary(package)

    assert "adapter.pyi:1 imports app.services" in str(exc.value)


def test_a_package_with_no_source_is_refused(tmp_path):
    """A zipped or byte-compiled install yields no source. Reporting that as a
    pass hands the caller a green check that read nothing."""
    empty = tmp_path / "compiled_only"
    empty.mkdir()
    (empty / "adapter.pyc").write_bytes(b"\x00")

    with pytest.raises(ExtensionValidationError) as exc:
        assert_import_boundary(empty)

    assert "no Python source" in str(exc.value)
