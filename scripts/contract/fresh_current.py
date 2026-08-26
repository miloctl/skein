from atlas_skein import AtlasSettings, atlas_module
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import private_notes

module = atlas_module(AtlasSettings("atlas-contract"))
with TestClient(create_app(modules=(module,))) as client:
    assert client.get("/health").status_code == 200
private_notes.add_note("fresh-owner", "fresh-person", "fresh schema marker")
