"""Published inventory counts stay bound to their executable registries."""

import ast
import asyncio
from pathlib import Path

from app import mcp_server
from app.services import fieldguide, insights, personas
from app.tools import ALL_TOOLS

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text()
FEATURES = (ROOT / "docs" / "FEATURES.md").read_text()
INSIGHTS = (ROOT / "docs" / "INSIGHTS.md").read_text()


def test_field_guide_claim_matches_the_registry():
    cards = fieldguide.registry()
    tieable = sum(card.get("ties", "predicate") != "never" for card in cards)
    assert f"{len(cards)} cards" in FEATURES
    assert f"{tieable} tieable" in FEATURES


def test_core_tool_claim_matches_the_registry():
    count = len(ALL_TOOLS)
    assert f"{count} Strands @tool wrappers" in README
    assert f"{count} Strands `@tool` wrappers" in FEATURES


def test_mcp_claim_matches_the_registry():
    tools = asyncio.run(mcp_server.mcp.list_tools())
    resources = asyncio.run(mcp_server.mcp.list_resources())
    assert [str(resource.uri) for resource in resources] == ["skein://context-pack"]
    assert f"{len(tools)} tools + the context-pack resource" in FEATURES


def test_findings_claim_matches_the_registry():
    tree = ast.parse(Path(insights.__file__).read_text())
    rule_ids = {
        call.args[0].value
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_finding"
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
    }
    assert f"{len(rule_ids)} rule IDs from {len(insights.RULES)} registry functions" in FEATURES
    assert f"The findings rules ({len(rule_ids)} rule IDs)" in INSIGHTS
    assert "**Plan drift**" in INSIGHTS


def test_stock_persona_claim_matches_the_registry():
    count = len(list(personas.PERSONAS_DIR.glob("*.md")))
    assert f"{count} curated specialist personas" in FEATURES
