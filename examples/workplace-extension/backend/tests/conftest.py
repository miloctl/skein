"""Compose Atlas against a disposable Skein data directory.

Set the environment before the first `app` import. Skein binds its
configuration when `app.config` imports, so a value set later has no
effect (docs/EXTENSIONS.md, "Package and deploy").
"""

import os
import tempfile
import uuid
from pathlib import Path

_DATA_DIR = Path(tempfile.mkdtemp(prefix="atlas-skein-test-"))
os.environ.setdefault("SKEIN_DATA_DIR", str(_DATA_DIR))
os.environ.setdefault("SKEIN_MODEL_PROVIDER", "mock")
os.environ.setdefault("SKEIN_SCHEDULER", "0")

# The imports stay below the environment setup on purpose: importing any
# `app` or `atlas_skein` module earlier would bind the default data paths.
import pytest  # noqa: E402

from atlas_skein.integration import AtlasItem, MemoryAtlasClient  # noqa: E402
from atlas_skein.module import AtlasSettings, atlas_module  # noqa: E402


@pytest.fixture()
def atlas(tmp_path):
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
        AtlasSettings(store_path=tmp_path / "atlas.db"),
        client=client,
    )
    return module, client
