"""The Docker image must carry every content directory the services read off
the package root. A missing one does not fail the build: personas/ and flocks/
degrade to an empty bench (no @ picker specialists, no flock fan-out) and only
fieldguide/ raises, so the gap reaches production as missing features rather
than a crash."""

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
DOCKERFILE = BACKEND / "Dockerfile"
# the idiom every content-dir constant uses, e.g. services/personas.py's
#   PERSONAS_DIR = Path(__file__).resolve().parent.parent.parent / "personas"
_ROOT_REL = re.compile(r'parent\.parent\.parent\s*/\s*"([^"]+)"')


def content_dirs() -> set[str]:
    """Directory names the app resolves from the package root, read out of the
    source so a newly added one is covered without editing this test."""
    found = set()
    for path in (BACKEND / "app").rglob("*.py"):
        found.update(_ROOT_REL.findall(path.read_text()))
    return found


def test_content_dirs_exist():
    assert content_dirs(), "the root-relative idiom changed — this test now pins nothing"
    for name in content_dirs():
        assert (BACKEND / name).is_dir(), f"app reads {name}/, which is not in the repo"


def test_dockerfile_copies_every_content_dir():
    body = DOCKERFILE.read_text()
    for name in content_dirs():
        assert re.search(rf"^COPY {re.escape(name)} \./{re.escape(name)}$", body, re.M), (
            f"Dockerfile does not COPY {name}/ — the image ships without it"
        )
