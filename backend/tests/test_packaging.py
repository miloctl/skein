"""The Docker build must carry every source directory used as wheel data.

The installed wheel resolves these files from ``skein_stock``. A Docker build
cannot create that wheel if one source directory is missing from its context.
"""

import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
DOCKERFILE = BACKEND / "Dockerfile"
CONTENT_DIRS = {"fieldguide", "flocks", "personas", "playbooks", "schemas"}


def content_dirs() -> set[str]:
    return CONTENT_DIRS


def test_content_dirs_exist():
    assert content_dirs(), "the root-relative idiom changed — this test now pins nothing"
    for name in content_dirs():
        assert (BACKEND / name).is_dir(), f"app reads {name}/, which is not in the repo"


def test_dockerfile_copies_every_content_dir():
    body = DOCKERFILE.read_text()
    for name in content_dirs():
        assert f"COPY {name} ./{name}" in body, (
            f"Dockerfile does not COPY {name}/ — the image ships without it"
        )


def test_public_python_contract_is_marked_as_typed():
    package_data = tomllib.loads((BACKEND / "pyproject.toml").read_text())["tool"]["setuptools"][
        "package-data"
    ]
    assert (BACKEND / "app" / "py.typed").is_file()
    assert "py.typed" in package_data["app"]
