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


def test_overlay_wins_a_slug_collision(fresh_db, tmp_path, monkeypatch):
    from app.services import playbooks

    shadowed = OVERLAY_YAML.replace("Vendor audit", "Local incident runbook")
    _overlay(tmp_path, monkeypatch, {"incident.yaml": shadowed})
    assert playbooks.get_playbook("incident")["name"] == "Local incident runbook"
    slugs = [p["slug"] for p in playbooks.list_playbooks()]
    assert slugs.count("incident") == 1


def test_no_overlay_keeps_the_stock_roster(fresh_db):
    from app.services import playbooks

    slugs = {p["slug"] for p in playbooks.list_playbooks()}
    assert {"incident", "prototype", "migration", "manager_onboarding"} <= slugs
    assert playbooks.get_playbook("incident")["milestones"]


def test_missing_overlay_dir_is_ignored(fresh_db, tmp_path, monkeypatch):
    from app import config
    from app.services import playbooks

    monkeypatch.setattr(config, "PLAYBOOKS_OVERLAY", tmp_path / "does-not-exist")
    assert {p["slug"] for p in playbooks.list_playbooks()} >= {"incident"}
