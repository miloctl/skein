"""Playbook loading: the stock directory plus the SKEIN_PLAYBOOKS_DIR overlay.
Overlay slugs join the roster, an overlay file with a stock slug wins, and no
overlay means the stock roster exactly."""

OVERLAY_YAML = """\
name: Vendor audit
description: Deployment-specific audit playbook
milestones:
  - title: Scope the audit
    tasks:
      - title: List the vendors
"""


def _overlay(tmp_path, monkeypatch, files):
    from app import config

    d = tmp_path / "playbooks-overlay"
    d.mkdir()
    for name, text in files.items():
        (d / name).write_text(text)
    monkeypatch.setattr(config, "PLAYBOOKS_OVERLAY", d)
    return d


def test_overlay_playbook_joins_the_roster_and_instantiates(fresh_db, tmp_path, monkeypatch):
    from app.services import playbooks

    _overlay(tmp_path, monkeypatch, {"vendor_audit.yaml": OVERLAY_YAML})
    roster = {p["slug"]: p for p in playbooks.list_playbooks()}
    assert "vendor_audit" in roster
    assert roster["vendor_audit"]["name"] == "Vendor audit"
    assert "incident" in roster  # stock roster still present

    result = playbooks.instantiate("vendor_audit", "Acme audit", actor="tester")
    assert result["milestones"] and result["engagement"]["name"] == "Acme audit"
    from app.services import work

    titles = [t["title"] for t in work.list_tasks()]
    assert "List the vendors" in titles  # the overlay's task really lands


def test_legacy_yaml_types_have_a_stable_collision_safe_digest(fresh_db, tmp_path, monkeypatch):
    from datetime import date

    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.services import playbooks

    _overlay(
        tmp_path,
        monkeypatch,
        {
            "legacy_types.yaml": """\
name: Legacy typed metadata
project_class: standard
private_metadata:
  review_date: 2026-08-11
  flags: !!set
    one: null
    two: null
  payload: !!binary |
    SGVsbG8=
  mixed_keys:
    1: integer
    "1": string
milestones:
  - title: Prepare
""",
        },
    )
    definition = playbooks.get_playbook("legacy_types")
    digest = playbooks.definition_digest(definition)
    assert digest == playbooks.definition_digest(definition)
    assert playbooks.definition_digest({"value": date(2026, 8, 11)}) != (
        playbooks.definition_digest({"value": "2026-08-11"})
    )

    with TestClient(create_app(), headers={"X-User": "mira"}) as client:
        response = client.post(
            "/api/playbooks/instantiate",
            json={"playbook": "legacy_types", "engagement_name": "Legacy typed delivery"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["engagement"]["name"] == "Legacy typed delivery"


def test_current_release_accepts_the_previous_digest_for_unchanged_content():
    import hashlib
    import json

    from app.services import playbooks

    definition = {
        "name": "Compatible delivery",
        "project_class": "standard",
        "milestones": [{"title": "Prepare"}],
    }
    previous = hashlib.sha256(
        json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert playbooks.definition_digest(definition).startswith("v2:")
    assert playbooks.definition_digest_matches(previous, definition)


def test_overlay_wins_a_slug_collision(fresh_db, tmp_path, monkeypatch):
    from app.services import playbooks

    shadowed = OVERLAY_YAML.replace("Vendor audit", "Local incident runbook")
    _overlay(tmp_path, monkeypatch, {"incident.yaml": shadowed})
    assert playbooks.get_playbook("incident")["name"] == "Local incident runbook"
    slugs = [p["slug"] for p in playbooks.list_playbooks()]
    assert slugs.count("incident") == 1


def test_no_overlay_keeps_the_stock_roster_exactly(fresh_db):
    """Equality, not subset — a leaked overlay would show up as an extra slug."""
    from app.services import playbooks

    slugs = {p["slug"] for p in playbooks.list_playbooks()}
    assert slugs == {"incident", "prototype", "migration", "manager_onboarding"}


def test_missing_overlay_dir_is_ignored(fresh_db, tmp_path, monkeypatch):
    from app import config
    from app.services import playbooks

    monkeypatch.setattr(config, "PLAYBOOKS_OVERLAY", tmp_path / "does-not-exist")
    assert {p["slug"] for p in playbooks.list_playbooks()} >= {"incident"}


def test_a_malformed_overlay_file_drops_off_the_roster(fresh_db, tmp_path, monkeypatch):
    """One broken operator file must not take down every playbook surface."""
    from app.services import playbooks

    _overlay(
        tmp_path,
        monkeypatch,
        {
            "vendor_audit.yaml": OVERLAY_YAML,
            "broken.yaml": "name: [unclosed",
            "empty.yaml": "",
            "listy.yaml": "- not\n- a\n- mapping\n",
        },
    )
    slugs = {p["slug"] for p in playbooks.list_playbooks()}
    assert "vendor_audit" in slugs
    assert not {"broken", "empty", "listy"} & slugs
    import pytest

    with pytest.raises(ValueError, match="malformed"):
        playbooks.get_playbook("broken")


def test_a_non_slug_stem_never_enters_the_roster(fresh_db, tmp_path, monkeypatch):
    from app.services import playbooks

    _overlay(tmp_path, monkeypatch, {"My Playbook.yaml": OVERLAY_YAML})
    assert all(" " not in p["slug"] for p in playbooks.list_playbooks())
