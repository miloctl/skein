"""Flocks: definition loading, the strict validator, and the overlay."""

import pytest

from app import config
from app.services import flocks, personas


def _write(d, slug, **fields):
    body = {
        "name": fields.pop("name", "Test Flock"),
        "description": fields.pop("description", "A flock for tests"),
        "members": fields.pop("members", ["code-reviewer", "backend-architect"]),
        **fields,
    }
    lines = []
    for k, v in body.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            lines += [f"  - {i}" for i in v]
        elif isinstance(v, bool):
            lines.append(f"{k}: {str(v).lower()}")
        else:
            lines.append(f"{k}: {v}")
    (d / f"{slug}.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def overlay(tmp_path, monkeypatch):
    """A flocks overlay directory, the SKEIN_FLOCKS_DIR deployment path."""
    monkeypatch.setattr(config, "FLOCKS_OVERLAY", tmp_path)
    return tmp_path


def test_stock_flocks_load_and_validate():
    roster = flocks.list_flocks()
    slugs = {f["slug"] for f in roster}
    assert {"engineering", "delivery"} <= slugs
    assert flocks.validate_all() == []


def test_stock_members_are_real_personas():
    bench = personas.bench_slugs()
    for f in flocks.list_flocks():
        assert flocks.MIN_MEMBERS <= len(f["members"]) <= flocks.MAX_MEMBERS
        for m in f["members"]:
            assert m["slug"] in bench
            assert m["name"] and m["emoji"]


def test_get_flock_returns_plain_member_slugs():
    f = flocks.get_flock("engineering")
    assert f["members"] == ["backend-architect", "code-reviewer", "minimal-change-engineer"]
    assert f["synthesis"] is False
    assert flocks.get_flock("delivery")["synthesis"] is True


def test_unknown_flock_names_the_roster():
    with pytest.raises(ValueError) as exc:
        flocks.get_flock("ghost")
    assert "engineering" in str(exc.value)


def test_off_charset_slug_is_not_echoed():
    """CLAUDE.md: an error never reflects the rejected value back."""
    bad = "Robert'); DROP TABLE--"
    with pytest.raises(ValueError) as exc:
        flocks.get_flock(bad)
    assert bad not in str(exc.value)
    assert "engineering" in str(exc.value)


def test_overlay_adds_a_flock(overlay):
    _write(overlay, "research", name="Research")
    assert "research" in {f["slug"] for f in flocks.list_flocks()}
    assert flocks.validate_all() == []


def test_overlay_wins_a_slug_collision(overlay):
    _write(overlay, "engineering", name="Re-cast Engineering")
    assert flocks.get_flock("engineering")["name"] == "Re-cast Engineering"


@pytest.mark.parametrize(
    "fields,expected",
    [
        ({"members": ["code-reviewer"]}, "1 entries"),
        (
            {
                "members": [
                    "code-reviewer",
                    "backend-architect",
                    "growth-mentor",
                    "meeting-notes",
                    "onboarding-guide",
                ]
            },
            "5 entries",
        ),
        ({"members": ["code-reviewer", "code-reviewer"]}, "repeats"),
        ({"members": ["code-reviewer", "ghost"]}, "not a persona"),
        ({"name": ""}, "name is empty"),
        ({"description": ""}, "description is empty"),
    ],
)
def test_bad_definitions_drop_off_and_fail_the_gate(overlay, fields, expected):
    """Lenient at runtime, loud in CI — the two must stay in step, or a file
    vanishes from the roster with no CI failure to explain it."""
    _write(overlay, "broken", **fields)
    assert "broken" not in {f["slug"] for f in flocks.list_flocks()}
    problems = flocks.validate_all()
    assert any(expected in p for p in problems), problems


def test_malformed_yaml_drops_off_and_fails_the_gate(overlay):
    (overlay / "broken.yaml").write_text("name: [unclosed\n", encoding="utf-8")
    assert "broken" not in {f["slug"] for f in flocks.list_flocks()}
    assert any("not valid YAML" in p for p in flocks.validate_all())


def test_persona_slug_collision_is_refused(overlay):
    """Synthesis logs usage under the FLOCK slug, so a flock named for a
    persona would bill that persona's row."""
    _write(overlay, "code-reviewer")
    assert "code-reviewer" not in {f["slug"] for f in flocks.list_flocks()}
    assert any("also a persona slug" in p for p in flocks.validate_all())


def test_missing_overlay_dir_surfaces_on_health(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "FLOCKS_OVERLAY", tmp_path / "gone")
    assert any("SKEIN_FLOCKS_DIR" in e for e in config.overlay_errors())
