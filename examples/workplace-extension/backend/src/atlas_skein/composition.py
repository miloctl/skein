"""One composition source for every Skein process this deployment starts.

The ASGI application imports `modules` here, and the standalone MCP server
resolves the same tuple through SKEIN_MCP_MODULES=atlas_skein.composition.
Two composition lists drift: the API process then enforces workplace policy
while the MCP process runs core-only.
"""

import os
from pathlib import Path

from .module import AtlasSettings, atlas_module

modules = (
    atlas_module(
        AtlasSettings(
            Path(os.getenv("ATLAS_SKEIN_DATA", "/atlas-data/atlas-extension.db")),
            api_url=os.getenv("ATLAS_API_URL", ""),
            api_token=os.getenv("ATLAS_API_TOKEN", ""),
        )
    ),
)
