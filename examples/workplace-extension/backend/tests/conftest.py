"""Compose Atlas against disposable Skein test storage.

Set the environment before the first `app` import. Skein binds its
configuration when `app.config` imports, so a value set later has no
effect (docs/EXTENSIONS.md, "Package and deploy").
"""

import os
import re
import tempfile
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

_DATABASE_URL = os.environ.get("SKEIN_DATABASE_URL", "")
if not _DATABASE_URL:
    raise RuntimeError("Set SKEIN_DATABASE_URL to a disposable PostgreSQL database before tests.")
_DATABASE_NAME = unquote(urlparse(_DATABASE_URL).path.rsplit("/", 1)[-1])
if not re.search(r"(?:^|[_.-])(?:test|tests|contract|scratch)(?:[_.-]|$)", _DATABASE_NAME):
    raise RuntimeError("Use a disposable database name that contains test, contract, or scratch.")

_DATA_DIR = Path(tempfile.mkdtemp(prefix="atlas-skein-test-"))
os.environ.setdefault("SKEIN_DATA_DIR", str(_DATA_DIR))
os.environ["SKEIN_AUTH_MODE"] = "trusted-header"
os.environ["SKEIN_MODEL_PROVIDER"] = "mock"
os.environ["SKEIN_SCHEDULER"] = "0"
os.environ["SKEIN_EMBEDDINGS"] = "0"

# Importing `app` or `atlas_skein` earlier binds configuration before test isolation is set.
import pytest  # noqa: E402
from atlas_skein.integration import AtlasItem, MemoryAtlasClient  # noqa: E402
from atlas_skein.module import AtlasSettings, atlas_module  # noqa: E402


@pytest.fixture()
def atlas():
    """One composed Atlas module with a fresh store and a fake remote.

    The core database is shared for the whole test session, so every test
    gets its own external item IDs and its own extension store.
    """
    suffix = uuid.uuid4().hex[:8]
    client = MemoryAtlasClient(
        (
            AtlasItem(f"ATLAS-{suffix}-1", f"Map the {suffix} boundary"),
            AtlasItem(f"ATLAS-{suffix}-2", f"Prove the {suffix} contract"),
        )
    )
    module = atlas_module(
        AtlasSettings(f"atlas-test-{suffix}"),
        client=client,
    )
    return module, client
