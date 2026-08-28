import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "verify_release_packages", ROOT / "scripts/verify_release_packages.py"
)
assert SPEC and SPEC.loader
verify_release_packages = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_release_packages)


def _wheel(directory: Path, version: str = "0.3.2", name: str = "skein-agents") -> Path:
    path = directory / f"skein_agents-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"skein_agents-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n",
        )
    return path


def _npm(directory: Path, package: str, version: str) -> Path:
    filename = f"{package.removeprefix('@miloctl/').replace('/', '-')}-{version}.tgz"
    path = directory / f"miloctl-{filename}"
    payload = json.dumps({"name": package, "version": version}).encode()
    info = tarfile.TarInfo("package/package.json")
    info.size = len(payload)
    with tarfile.open(path, "w:gz") as archive:
        archive.addfile(info, io.BytesIO(payload))
    return path


def _artifact(directory: Path, host_version: str = "0.3.2"):
    directory.mkdir(parents=True, exist_ok=True)
    _wheel(directory)
    _npm(directory, "@miloctl/skein-extension-api", "1.0.0")
    _npm(directory, "@miloctl/skein-frontend-host", host_version)
    return directory


def test_release_artifact_has_exact_package_identities(tmp_path):
    release = verify_release_packages.inspect_artifact(_artifact(tmp_path))
    assert release.version == "0.3.2"
    assert release.tag == "v0.3.2"
    assert release.extension_version == "1.0.0"
    assert release.wheel.name == "skein_agents-0.3.2-py3-none-any.whl"


def test_release_artifact_refuses_an_extra_file(tmp_path):
    _artifact(tmp_path)
    (tmp_path / "extra.txt").write_text("unexpected")
    with pytest.raises(verify_release_packages.VerificationError, match="exactly three"):
        verify_release_packages.inspect_artifact(tmp_path)


def test_release_artifact_refuses_a_wrong_identity(tmp_path):
    _artifact(tmp_path)
    (tmp_path / "skein_agents-0.3.2-py3-none-any.whl").unlink()
    _wheel(tmp_path, name="other-package")
    with pytest.raises(verify_release_packages.VerificationError, match="identity"):
        verify_release_packages.inspect_artifact(tmp_path)


def test_release_artifact_refuses_a_host_version_mismatch(tmp_path):
    _artifact(tmp_path, host_version="0.3.1")
    with pytest.raises(verify_release_packages.VerificationError, match="versions"):
        verify_release_packages.inspect_artifact(tmp_path)


@pytest.mark.parametrize("changed", ("wheel", "api", "host"))
def test_registry_bytes_must_match_the_original_artifact(tmp_path, changed):
    original = _artifact(tmp_path / "original")
    registry = _artifact(tmp_path / "registry")
    release = verify_release_packages.inspect_artifact(original)
    selected = {
        "wheel": release.wheel,
        "api": release.extension_api,
        "host": release.frontend_host,
    }[changed]
    with (registry / selected.name).open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(verify_release_packages.VerificationError, match="bytes"):
        verify_release_packages.compare_registry_bytes(release, registry)


def test_registry_bytes_accept_exact_files(tmp_path):
    original = _artifact(tmp_path / "original")
    registry = _artifact(tmp_path / "registry")
    release = verify_release_packages.inspect_artifact(original)
    verify_release_packages.compare_registry_bytes(release, registry)
