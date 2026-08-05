"""The JSON export covers every table. A new table without a row here is the failure mode."""


def test_export_covers_the_newer_tables(fresh_db):
    from app.services import absences, admin, users

    users.ensure_user("mira")
    absences.add_absence("mira", "2026-08-03", "2026-08-04", actor="mira")
    out = admin.export()
    assert out["tables"]["absences"] == 1
    for table in ("task_worklog", "finding_dispositions", "app_settings", "job_outcomes"):
        assert table in out["tables"]


def test_export_covers_new_tables(fresh_db):
    from app.services import admin

    out = admin.export()
    for t in (
        "commitments",
        "agent_authority",
        "findings",
        "tool_usage",
        "context_packs",
        "forecast_snapshots",
    ):
        assert t in out["tables"]
    assert "api_keys" not in out["tables"]  # hashes must not travel


def test_export_accounts_for_every_table(fresh_db):
    """A migration that adds a table decides its export fate HERE, not by
    accident: feature_unlocks and mention_log fell out of exports for five
    migrations because nothing noticed absence. FTS shadow tables
    (search_index_*) ride with their virtual table's exclusion."""
    from app.services.admin import EXCLUDED, TABLES

    rows = fresh_db.query(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
        " AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'search_index_%'"
    )
    real = {r["name"] for r in rows}
    unaccounted = real - set(TABLES) - EXCLUDED
    assert not unaccounted, (
        f"tables neither exported nor excluded-with-reason: {sorted(unaccounted)}"
    )
    ghosts = (set(TABLES) | EXCLUDED) - real
    assert not ghosts, f"TABLES/EXCLUDED name tables that do not exist: {sorted(ghosts)}"
