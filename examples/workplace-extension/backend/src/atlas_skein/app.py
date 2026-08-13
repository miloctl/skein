"""Private deployment composition root."""

from app.main import create_app

from .composition import modules

app = create_app(modules=modules)
