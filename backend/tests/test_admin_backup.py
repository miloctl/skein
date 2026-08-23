"""Database recovery dumps, mirror status, and artifact-volume boundaries."""

import shutil
import threading
import time
from pathlib import Path

import pytest


def _pg_restore() -> str:
    found = shutil.which("pg_restore")
    assert found, "pg_restore must be installed to drill a restore"
    return found


def _dump_list(path, admin) -> str:
    import subprocess

    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        [_pg_restore(), "--list", str(path)],
        env=admin._pg_env(),
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_backup_conninfo_preserves_tls_and_routing_without_credentials(fresh_db, monkeypatch):
    from psycopg.conninfo import conninfo_to_dict

    from app import config
    from app.services import admin

    monkeypatch.setattr(
        config,
        "DATABASE_URL",
        "host=db.example port=5433 dbname=skein user=app password=secret"
        " sslmode=verify-full sslrootcert=/ca.pem sslpassword=certsecret"
        " connect_timeout=7 target_session_attrs=read-write",
    )
    info = conninfo_to_dict(admin._pg_conninfo())
    assert info == {
        "host": "db.example",
        "port": "5433",
        "dbname": "skein",
        "user": "app",
        "sslmode": "verify-full",
        "sslrootcert": "/ca.pem",
        "connect_timeout": "7",
        "target_session_attrs": "read-write",
    }
    env = admin._pg_env()
    assert env["PGPASSWORD"] == "secret"
    assert env["PGSSLPASSWORD"] == "certsecret"
    assert "secret" not in admin._pg_conninfo()


def test_missing_configured_mirror_is_not_manufactured(fresh_db, tmp_path, monkeypatch):
    from app.services import admin

    mirror = tmp_path / "not-mounted"
    monkeypatch.setenv("SKEIN_BACKUP_MIRROR", str(mirror))
    result = admin.backup()
    assert result["status"] == "partial"
    assert result["mirror_status"] == "unavailable"
    assert result["mirrored_platform_path"] is None
    assert result["artifacts_included"] is False
    assert Path(result["database_path"]).exists()
    assert not mirror.exists()


def test_mirror_cannot_alias_the_local_backup_directory(fresh_db, monkeypatch):
    from app.services import admin

    local = admin._backups_dir()
    monkeypatch.setenv("SKEIN_BACKUP_MIRROR", str(local))
    result = admin.backup()
    assert result["status"] == "partial"
    assert result["mirror_status"] == "unavailable"
    assert result["mirrored_platform_path"] is None


def test_existing_mirror_works_with_a_custom_data_dir(fresh_db, tmp_path, monkeypatch):
    from app import config
    from app.services import admin

    data = tmp_path / "custom-data"
    data.mkdir()
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setenv("SKEIN_BACKUP_MIRROR", str(mirror))
    monkeypatch.setattr(admin, "_separate_mirror_filesystem", lambda _target: True)

    result = admin.backup()
    assert result["status"] == "ok"
    assert result["mirror_status"] == "written"
    assert result["mirror_scope"] == "public_schema"
    assert Path(result["mirrored_platform_path"]).parent == mirror
    assert Path(result["mirrored_platform_path"]).exists()
    assert Path(result["mirrored_platform_path"]).stat().st_mode & 0o077 == 0
    assert Path(result["database_path"]).exists()
    assert Path(result["database_path"]).stat().st_mode & 0o077 == 0
    assert len(list(admin._backups_dir().glob("platform-*.dump"))) == 1


def test_backup_hardens_and_bounds_legacy_files(fresh_db):
    from app.services import admin

    backups = admin._backups_dir()
    for day in range(1, 16):
        for path in (
            backups / f"platform-2000-01-{day:02}.db",
            backups / f"private-2000-01-{day:02}.dump",
        ):
            path.write_bytes(b"legacy")
            path.chmod(0o644)
    old_temp = backups / "private-2000-01-01.dump.tmp"
    current_temp = backups / "extension-old-2000-01-01.dump.dead.tmp"
    old_temp.write_bytes(b"partial")
    current_temp.write_bytes(b"partial")

    result = admin.backup(keep=14)
    assert backups.stat().st_mode & 0o077 == 0
    legacy_db = list(backups.glob("platform-*.db"))
    private = list(backups.glob("private-*.dump"))
    # Thirteen old suffixes plus the new combined database/platform suffix.
    assert len(legacy_db) == len(private) == 13
    assert result["kept"] == 14
    units = {
        match.group(2)
        for path in backups.iterdir()
        if (match := admin._BACKUP_FILE.fullmatch(path.name))
    }
    assert len(units) == 14
    assert all(path.stat().st_mode & 0o077 == 0 for path in legacy_db + private)
    assert not old_temp.exists()
    assert not current_temp.exists()


def test_local_retention_always_keeps_the_current_dump(fresh_db):
    from app.services import admin

    future = admin._backups_dir() / "database-9999-12-31.dump"
    future.write_bytes(b"future")
    result = admin.backup(keep=1)
    assert Path(result["database_path"]).exists()
    assert not future.exists()


def test_mirror_retention_keeps_manual_recovery_points(fresh_db, tmp_path, monkeypatch):
    from app.services import admin

    mirror = tmp_path / "mirror"
    mirror.mkdir()
    manual = mirror / "platform-pre-upgrade.dump"
    manual.write_bytes(b"manual")
    stale = mirror / "platform-9999-01-01.dump.dead.tmp"
    stale.write_bytes(b"stale")
    for day in range(1, 32):
        (mirror / f"platform-9999-01-{day:02}.dump").write_bytes(b"future")
    monkeypatch.setenv("SKEIN_BACKUP_MIRROR", str(mirror))
    monkeypatch.setattr(admin, "_separate_mirror_filesystem", lambda _target: True)

    result = admin.backup()
    assert result["mirror_status"] == "written"
    assert Path(result["mirrored_platform_path"]).exists()
    assert manual.exists()
    assert not stale.exists()
    dated = [
        path
        for path in mirror.glob("platform-*.dump")
        if admin._DATED_BACKUP.fullmatch(path.name.removeprefix("platform-"))
    ]
    assert len(dated) == 30


def test_mirror_hardens_and_bounds_legacy_database_files(fresh_db, tmp_path, monkeypatch):
    from app.services import admin

    mirror = tmp_path / "mirror"
    mirror.mkdir()
    for day in range(1, 32):
        path = mirror / f"platform-2000-01-{day:02}.db"
        path.write_bytes(b"legacy")
        path.chmod(0o644)
    stale = mirror / "private-2000-01-01.dump.tmp"
    stale.write_bytes(b"partial")
    monkeypatch.setenv("SKEIN_BACKUP_MIRROR", str(mirror))
    monkeypatch.setattr(admin, "_separate_mirror_filesystem", lambda _target: True)

    result = admin.backup()
    legacy = list(mirror.glob("platform-*.db"))
    assert len(legacy) == 29
    assert Path(result["mirrored_platform_path"]).exists()
    units = {
        match.group(2)
        for path in mirror.iterdir()
        if (match := admin._BACKUP_FILE.fullmatch(path.name))
    }
    assert len(units) == 30
    assert all(path.stat().st_mode & 0o077 == 0 for path in legacy)
    assert not stale.exists()


def test_mirror_copy_failure_is_partial_and_cleans_the_temp_file(fresh_db, tmp_path, monkeypatch):
    from app.services import admin

    mirror = tmp_path / "mirror"
    mirror.mkdir()
    monkeypatch.setenv("SKEIN_BACKUP_MIRROR", str(mirror))
    monkeypatch.setattr(admin, "_separate_mirror_filesystem", lambda _target: True)
    original = admin.subprocess.run

    def fail_copy(args, **kwargs):
        if Path(args[0]).name == "cp":
            raise admin.subprocess.CalledProcessError(1, args)
        return original(args, **kwargs)

    monkeypatch.setattr(admin.subprocess, "run", fail_copy)
    result = admin.backup()
    assert result["status"] == "partial"
    assert result["mirror_status"] == "unavailable"
    assert list(mirror.glob("*.tmp")) == []


def test_unconfigured_mirror_is_an_explicit_success_state(fresh_db):
    from app.services import admin

    result = admin.backup()
    assert result["status"] == "ok"
    assert result["mirror_status"] == "not_configured"
    assert result["mirrored_platform_path"] is None


def test_backup_fsyncs_and_removes_stale_temp_files(fresh_db, monkeypatch):
    from app.services import admin

    backups = admin._backups_dir()
    stale = backups / "database-2000-01-01.dump.dead.tmp"
    stale.write_bytes(b"stale")
    calls = []
    monkeypatch.setattr(admin.os, "fsync", lambda fd: calls.append(fd))

    admin.backup()
    assert not stale.exists()
    assert len(calls) >= 4


def test_backup_timeout_is_retryable_and_cleans_temp_files(fresh_db, monkeypatch):
    import subprocess

    from psycopg.errors import LockNotAvailable

    from app.services import admin

    def timeout(*_args, **kwargs):
        assert kwargs["timeout"] == admin._BACKUP_RUN_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired("pg_dump", kwargs["timeout"])

    monkeypatch.setattr(admin.subprocess, "run", timeout)
    with pytest.raises(LockNotAvailable, match="timed out"):
        admin.backup()
    assert list(admin._backups_dir().glob("*.tmp")) == []


def test_backup_lock_wait_is_bounded(fresh_db, monkeypatch):
    from psycopg.errors import LockNotAvailable

    from app.services import admin

    monkeypatch.setattr(admin, "_BACKUP_LOCK_WAIT_SECONDS", 0.01)
    admin._BACKUP_LOCK.acquire()
    try:
        with pytest.raises(LockNotAvailable, match="still running"):
            admin.backup()
    finally:
        admin._BACKUP_LOCK.release()


def test_backup_filesystem_lock_blocks_another_process(fresh_db, monkeypatch):
    import subprocess
    import sys

    from psycopg.errors import LockNotAvailable

    from app.services import admin

    lock_path = admin._backups_dir() / ".backup.lock"
    holder = subprocess.Popen(  # noqa: S603 — fixed interpreter and literal program
        [
            sys.executable,
            "-c",
            "import fcntl,sys; f=open(sys.argv[1],'a+');"
            "fcntl.flock(f.fileno(),fcntl.LOCK_EX);print('ready',flush=True);sys.stdin.read()",
            str(lock_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout and holder.stdout.readline().strip() == "ready"
        monkeypatch.setattr(admin, "_BACKUP_LOCK_WAIT_SECONDS", 0.05)
        monkeypatch.setattr(
            admin,
            "_backup",
            lambda **_kwargs: pytest.fail("pg_dump path ran while flock was held"),
        )
        with pytest.raises(LockNotAvailable, match="still running"):
            admin.backup()
    finally:
        if holder.stdin:
            holder.stdin.close()
        holder.wait(timeout=3)


def test_backup_workflow_is_serialized(fresh_db, monkeypatch):
    from app.services import admin

    active = 0
    maximum = 0
    guard = threading.Lock()
    barrier = threading.Barrier(4)

    def fake_backup(*, keep, actor):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.05)
        with guard:
            active -= 1
        return {"keep": keep, "actor": actor}

    monkeypatch.setattr(admin, "_backup", fake_backup)
    workers = [threading.Thread(target=lambda: (barrier.wait(), admin.backup())) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=3)
    assert maximum == 1


def test_database_dump_contains_private_and_opted_in_extension_schemas(fresh_db):
    from app import config
    from app.services import admin, private_notes

    private_notes.add_note("manager", "dana", "irreplaceable 1:1 note")
    fresh_db.execute("CREATE SCHEMA ext_kept")
    fresh_db.execute("CREATE TABLE ext_kept.records (value text)")
    fresh_db.execute("INSERT INTO ext_kept.records VALUES ('kept')")
    fresh_db.execute("CREATE SCHEMA ext_skipped")
    fresh_db.execute("CREATE TABLE ext_skipped.records (value text)")
    admin.set_extension_stores(
        {"kept": "ext_kept", "skipped": "ext_skipped"},
        {"kept"},
    )
    try:
        result = admin.backup()
        listing = _dump_list(result["database_path"], admin)
        assert f" {config.PRIVATE_SCHEMA} " in listing
        assert " ext_kept " in listing
        assert " ext_skipped " not in listing
        assert result["private_path"] is None
        assert result["extension_paths"] == []
    finally:
        admin.set_extension_stores({}, set())


def test_restore_drill_recovers_one_database_unit_and_requires_artifact_volume(
    scratch_db, tmp_path, monkeypatch
):
    """Drill atomic archive load, schema data, and artifact files.

    The test server uses its bootstrap superuser. The rendered deployment
    contract separately pins schema pre-creation and forbids database-wide
    CREATE for the application role.
    """
    import subprocess

    from app import config, db
    from app.services import activity, admin, documents, handoff, private_notes, work

    live_data = tmp_path / "live-data"
    recovery = tmp_path / "recovery-copy"
    recovery.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", live_data)
    restore = _pg_restore()
    task = work.create_task(title="survives the restore", actor="tester")
    private_notes.add_note("manager", "dana", "the private note that must survive")
    db.execute("CREATE SCHEMA ext_probe")
    db.execute("CREATE TABLE ext_probe.records (value text)")
    db.execute("INSERT INTO ext_probe.records VALUES ('extension survives')")
    db.execute("CREATE SCHEMA ext_skipped")
    db.execute("CREATE TABLE ext_skipped.records (value text)")
    admin.set_extension_stores(
        {"probe": "ext_probe", "skipped": "ext_skipped"},
        {"probe"},
    )
    document = documents.create_document(
        "probe",
        "artifact survives only with its volume",
        actor="tester",
    )
    artifact_root = live_data / "artifacts"
    artifact_copy = recovery / "artifacts"
    shutil.copytree(artifact_root, artifact_copy)
    for index in range(2):
        db.log_activity("tester", "probe", f"chained row {index}")
    assert activity.verify_chain()["ok"] is True

    try:
        result = admin.backup()
        database_copy = recovery / "database.dump"
        shutil.copy2(result["database_path"], database_copy)
        shutil.rmtree(live_data)
        db.execute("DROP SCHEMA public CASCADE")
        db.execute(f"DROP SCHEMA {config.PRIVATE_SCHEMA} CASCADE")
        db.execute("DROP SCHEMA ext_probe CASCADE")
        db.execute("DROP SCHEMA ext_skipped CASCADE")
        db.execute("CREATE SCHEMA public")
        db.execute(f"CREATE SCHEMA {config.PRIVATE_SCHEMA}")
        db.execute("CREATE SCHEMA ext_probe")
        private_notes._schema_ready = False

        env = admin._pg_env()
        load = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [
                restore,
                "--dbname",
                env["PGDATABASE"],
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                "--no-comments",
                "--single-transaction",
                "--exit-on-error",
                str(database_copy),
            ],
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert load.returncode == 0, load.stderr
        db.close_pool()

        restored = db.query_one("SELECT title FROM tasks WHERE id = ?", (task["id"],))
        assert restored == {"title": "survives the restore"}
        notes = private_notes.list_notes("manager", "dana")
        assert [note["body"] for note in notes] == ["the private note that must survive"]
        assert db.query_row("SELECT value FROM ext_probe.records")["value"] == "extension survives"
        assert (
            db.query_one(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'ext_skipped'"
            )
            is None
        )
        with pytest.raises(handoff.ArtifactUnreadable):
            handoff.read_artifact(document["id"])
        shutil.copytree(artifact_copy, artifact_root)
        restored_artifact = handoff.read_artifact(document["id"])
        assert restored_artifact["markdown"] == "artifact survives only with its volume"
        assert activity.verify_chain()["ok"] is True
    finally:
        admin.set_extension_stores({}, set())


def test_mirror_only_recovery_is_explicitly_partial(scratch_db, tmp_path, monkeypatch):
    import subprocess

    from app import config, db
    from app.services import admin, documents, private_notes, work

    live_data = tmp_path / "live-data"
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", live_data)
    monkeypatch.setenv("SKEIN_BACKUP_MIRROR", str(mirror))
    monkeypatch.setattr(admin, "_separate_mirror_filesystem", lambda _target: True)
    task = work.create_task("public task", actor="tester")
    private_notes.add_note("manager", "dana", "lost private note")
    db.execute("CREATE SCHEMA ext_probe")
    db.execute("CREATE TABLE ext_probe.records (value text)")
    db.execute("INSERT INTO ext_probe.records VALUES ('lost extension row')")
    admin.set_extension_stores({"probe": "ext_probe"}, {"probe"})
    documents.create_document("probe", "lost artifact body", actor="tester")

    try:
        result = admin.backup()
        mirror_copy = tmp_path / "platform.dump"
        shutil.copy2(result["mirrored_platform_path"], mirror_copy)
        shutil.rmtree(live_data)
        db.execute("DROP SCHEMA public CASCADE")
        db.execute(f"DROP SCHEMA {config.PRIVATE_SCHEMA} CASCADE")
        db.execute("DROP SCHEMA ext_probe CASCADE")
        db.execute("CREATE SCHEMA public")
        private_notes._schema_ready = False

        env = admin._pg_env()
        load = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [
                _pg_restore(),
                "--dbname",
                env["PGDATABASE"],
                "--clean",
                "--if-exists",
                "--no-owner",
                "--single-transaction",
                "--exit-on-error",
                str(mirror_copy),
            ],
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert load.returncode == 0, load.stderr
        db.close_pool()

        assert db.query_row("SELECT title FROM tasks WHERE id = ?", (task["id"],))["title"] == (
            "public task"
        )
        assert (
            db.query_one(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name = ?",
                (config.PRIVATE_SCHEMA,),
            )
            is None
        )
        assert (
            db.query_one(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'ext_probe'"
            )
            is None
        )
        assert db.query_row("SELECT COUNT(*) AS count FROM artifacts")["count"] == 1
        db.execute("TRUNCATE artifacts RESTART IDENTITY CASCADE")
        assert db.query_row("SELECT COUNT(*) AS count FROM artifacts")["count"] == 0
        assert private_notes.list_notes("manager", "dana") == []
        db.execute("CREATE SCHEMA ext_probe")
        db.execute("CREATE TABLE ext_probe.records (value text)")
        assert db.query_row("SELECT COUNT(*) AS count FROM ext_probe.records")["count"] == 0
    finally:
        admin.set_extension_stores({}, set())


def test_manual_backup_route_has_a_deployment_wide_cap(client, monkeypatch):
    from app.services import admin
    from app.services.api_keys import create_key

    monkeypatch.setattr(
        admin,
        "backup",
        lambda **_kwargs: {
            "status": "ok",
            "database_path": "/tmp/probe.dump",
            "mirror_status": "not_configured",
            "artifacts_included": False,
        },
    )
    headers = {"Authorization": f"Bearer {create_key('tester', 'backup')['key']}"}
    assert client.post("/api/admin/backup", headers=headers).status_code == 200
    assert client.post("/api/admin/backup", headers=headers).status_code == 200
    limited = client.post("/api/admin/backup", headers=headers)
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0


def test_same_day_manual_backups_are_immutable(fresh_db):
    from app.services import admin

    first = admin.backup(actor="ava")
    second = admin.backup(actor="ava")
    assert first["backup_id"] != second["backup_id"]
    assert Path(first["database_path"]).exists()
    assert Path(second["database_path"]).exists()


def test_backup_if_stale_reports_noop_and_interrupted_claim(fresh_db):
    from app import db
    from app.services import admin

    admin.backup()
    assert admin.backup_if_stale() == {"status": "noop"}

    for path in admin._backups_dir().glob(f"database-{admin._today()}*.dump"):
        path.unlink()
    assert db.claim_job("backup", admin._today())
    result = admin.backup_if_stale()
    assert result["status"] == "error"
    assert "incomplete" in result["reason"]


def test_configured_mirror_failure_is_a_failed_job_outcome(fresh_db, tmp_path, monkeypatch):
    from app import db
    from app.services import jobs

    monkeypatch.setenv("SKEIN_BACKUP_MIRROR", str(tmp_path / "missing"))
    spec = next(item for item in jobs.JOBS if item.name == "daily-backup")
    jobs.run_job(spec)
    outcome = db.query_row(
        "SELECT status, detail FROM job_outcomes WHERE job = 'daily-backup'"
        " ORDER BY id DESC LIMIT 1"
    )
    assert outcome["status"] == "error"
    assert "status=partial" in outcome["detail"]


def test_manual_backup_ties_the_knot_and_scheduled_does_not(fresh_db):
    from app.services import admin, fieldguide

    fresh_db.execute(
        "INSERT INTO users (name, kind, active, created_at) VALUES (?, 'human', 1, ?)",
        ("ava", fresh_db.now()),
    )
    admin.backup()
    assert not fieldguide.PREDICATES["backup"]("ava")

    result = admin.backup(actor="ava")
    assert fieldguide.PREDICATES["backup"]("ava")
    row = fresh_db.query_one(
        "SELECT detail FROM activity WHERE actor = 'ava' AND action = 'backup'"
    )
    assert row and row["detail"] == Path(result["database_path"]).name
