"""Private deployment composition root."""

import os
from pathlib import Path

from app.main import create_app

from .module import AtlasSettings, atlas_module

app = create_app(
    modules=(
        atlas_module(
            AtlasSettings(
                Path(os.getenv("ATLAS_SKEIN_DATA", "/atlas-data/atlas-extension.db")),
                api_url=os.getenv("ATLAS_API_URL", ""),
                api_token=os.getenv("ATLAS_API_TOKEN", ""),
            )
        ),
    )
)
