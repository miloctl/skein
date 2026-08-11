"""Version 1 compatibility and deployment content validation."""

from pathlib import Path


def test_unversioned_stock_content_is_version_one(fresh_db):
    from app.services import flocks, personas, playbooks

    assert playbooks.get_playbook("prototype")["schema_version"] == 1
    assert personas.get_persona("backend-architect")["schema_version"] == 1
    assert flocks.get_flock("delivery")["schema_version"] == 1


def test_deployment_validator_accepts_versioned_overlay_content(fresh_db, tmp_path, monkeypatch):
    from app import config, content

    playbook_dir = tmp_path / "playbooks"
    persona_dir = tmp_path / "personas"
    flock_dir = tmp_path / "flocks"
    for directory in (playbook_dir, persona_dir, flock_dir):
        directory.mkdir()
    (playbook_dir / "atlas.yaml").write_text(
        "schema_version: 1\nname: Atlas\nmilestones:\n  - title: Start\n"
    )
    (persona_dir / "atlas-reviewer.md").write_text(
        "---\nschema_version: 1\nname: Atlas Reviewer\n"
        "description: Reviews Atlas work\n---\nReview the work.\n"
    )
    (flock_dir / "atlas-team.yaml").write_text(
        "schema_version: 1\nname: Atlas Team\ndescription: Two views\n"
        "members:\n  - atlas-reviewer\n  - backend-architect\n"
    )
    monkeypatch.setattr(config, "PLAYBOOKS_OVERLAY", playbook_dir)
    monkeypatch.setattr(config, "PERSONAS_OVERLAY", persona_dir)
    monkeypatch.setattr(config, "FLOCKS_OVERLAY", flock_dir)
    assert content.validate() == []


def test_deployment_validator_rejects_future_versions_and_unknown_fields(
    fresh_db, tmp_path, monkeypatch
):
    from app import config, content

    playbook_dir = tmp_path / "playbooks"
    persona_dir = tmp_path / "personas"
    flock_dir = tmp_path / "flocks"
    for directory in (playbook_dir, persona_dir, flock_dir):
        directory.mkdir()
    (playbook_dir / "atlas.yaml").write_text("schema_version: 2\nname: Atlas\nprivate_hook: yes\n")
    (persona_dir / "atlas-reviewer.md").write_text(
        "---\nschema_version: 2\nname: Atlas Reviewer\n"
        "description: Reviews Atlas work\n---\nReview the work.\n"
    )
    (flock_dir / "atlas-team.yaml").write_text(
        "schema_version: 2\nname: Atlas Team\ndescription: Two views\n"
        "members:\n  - backend-architect\n  - code-reviewer\nprivate_hook: yes\n"
    )
    monkeypatch.setattr(config, "PLAYBOOKS_OVERLAY", playbook_dir)
    monkeypatch.setattr(config, "PERSONAS_OVERLAY", persona_dir)
    monkeypatch.setattr(config, "FLOCKS_OVERLAY", flock_dir)
    errors = content.validate()
    assert any("playbook" in error and "schema_version" in error for error in errors)
    assert any("persona" in error and "schema_version" in error for error in errors)
    assert any("flock" in error and "schema_version" in error for error in errors)
    assert any("unknown top-level" in error for error in errors)


def test_content_validation_cli_accepts_explicit_directories(fresh_db, tmp_path, monkeypatch):
    from app import content

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(
        "sys.argv",
        [
            "content",
            "--playbooks",
            str(empty),
            "--personas",
            str(empty),
            "--flocks",
            str(empty),
        ],
    )
    assert content.main() == 0
    assert isinstance(empty, Path)
