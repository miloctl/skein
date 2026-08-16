"""Field-guide tool: current product guidance for specialist agents."""

import json

from strands import tool

from ..services import fieldguide


@tool
def field_guide() -> str:
    """Read Skein's current field guide, including instructions and in-app links."""
    fields = ("id", "feature", "knot", "pitch", "how", "link")
    return json.dumps([{key: card[key] for key in fields} for card in fieldguide.registry()])
