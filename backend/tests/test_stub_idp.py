import runpy
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "stub-idp.py"


def test_an_explicit_group_map_fails_closed_for_unknown_users(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["stub-idp.py", "8610", "skein", '{"mira":["atlas-delivery-managers"]}'],
    )
    groups_for = runpy.run_path(str(SCRIPT))["groups_for"]
    assert groups_for("mira") == ["atlas-delivery-managers"]
    assert groups_for("typo") == []


def test_an_explicit_empty_group_map_grants_no_groups(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["stub-idp.py", "8610", "skein", "{}"])
    groups_for = runpy.run_path(str(SCRIPT))["groups_for"]
    assert groups_for("ava") == []


def test_the_default_browser_walk_keeps_its_admin_group(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["stub-idp.py", "8610", "skein"])
    groups_for = runpy.run_path(str(SCRIPT))["groups_for"]
    assert groups_for("ava") == ["skein-admins"]
