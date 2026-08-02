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
