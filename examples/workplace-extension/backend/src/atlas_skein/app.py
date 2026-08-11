"""Private deployment composition root."""

import os
from pathlib import Path

from app.main import create_app

from .module import AtlasSettings, atlas_module

app = create_app(
    modules=(
        atlas_module(
            AtlasSettings(Path(os.getenv("ATLAS_SKEIN_DATA", "/data/atlas-extension.db")))
        ),
    )
)
