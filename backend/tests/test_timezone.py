"""The team zone (SKEIN_TZ): which day "today" means, and which hour a ritual
fires. Storage stays UTC — these pin that the two never get confused."""

import importlib
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app import config, db


def _reload(monkeypatch, value):
    monkeypatch.setenv("SKEIN_TZ", value)
    return importlib.reload(config)


def test_default_is_utc(monkeypatch):
    # "" and not delenv, for the reason conftest.py records: the reload runs
    # load_dotenv(), which re-fills an ABSENT variable from backend/.env.
    cfg = _reload(monkeypatch, "")
    assert cfg.TZ_NAME == "UTC"
    assert cfg.TZ_ERROR == ""


def test_a_bad_zone_degrades_to_utc_and_says_so(monkeypatch):
    """The model-provider discipline: a typo must not take down the API.

    A well-shaped name with no zone behind it is a SPELLING fault, and it must
    not be reported as the shape fault below — an operator who reads "not a
    Region/City name" about America/New_Yrok goes looking for the wrong
    mistake."""
    cfg = _reload(monkeypatch, "Mars/Olympus_Mons")
    assert cfg.TZ_NAME == "UTC"
    assert "spelling" in cfg.TZ_ERROR
    assert "Skein uses UTC" in cfg.TZ_ERROR
    # the rejected value goes to the LOG, never the body: TZ_ERROR is served
    # on /health, and AUTH_ERROR makes the same choice for the same reason
    assert "Mars/Olympus_Mons" not in cfg.TZ_ERROR


def test_now_stays_utc_whatever_the_zone(monkeypatch):
    """Storage contract: every stored timestamp is UTC ISO-8601, so a zone
    change never makes yesterday's rows unreadable."""
    _reload(monkeypatch, "America/New_York")
    assert db.now().endswith("+00:00")


def test_today_is_the_team_day_not_the_utc_day(monkeypatch):
    """The case that motivates the whole change: at 21:00 in New York it is
    already tomorrow in UTC, and a due-today list must not skip a day."""
    _reload(monkeypatch, "America/New_York")
    ny = ZoneInfo("America/New_York")
    fixed = datetime(2026, 8, 8, 23, 30, tzinfo=ny)  # 03:30 UTC on the 9th

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed

    monkeypatch.setattr(db, "datetime", _FixedDatetime)
    assert db.today() == date(2026, 8, 8)
    assert db.now()[:10] == "2026-08-09"  # the UTC day, deliberately different


def test_local_midnight_utc_is_an_instant(monkeypatch):
    """created_at bounds are timestamps. A bare local date there anchors the
    window to UTC midnight and drops the evening's work west of UTC."""
    _reload(monkeypatch, "America/New_York")
    got = db.local_midnight_utc(date(2026, 8, 8))
    assert datetime.fromisoformat(got) == datetime(2026, 8, 8, 4, 0, tzinfo=UTC)  # EDT is UTC-4


def test_scheduler_runs_in_the_team_zone(monkeypatch):
    """The hours in JOBS are the hours a person experiences: the 07:00 digest
    is 07:00 where the team works, not 07:00 UTC.

    Asserts against the REAL wiring. A test that builds its own CronTrigger
    tests APScheduler, and a test that greps the source pins the spelling —
    both pass with the feature reverted, which was the state this replaced."""
    _reload(monkeypatch, "America/New_York")
    from app import main

    captured = {}

    class _Fake:
        def __init__(self, **kw):
            captured.update(kw)
            self.jobs = []

        def add_job(self, *a, **kw):
            self.jobs.append(kw)

        def start(self):
            captured["started"] = True

    import apscheduler.schedulers.background as bg

    monkeypatch.setattr(bg, "BackgroundScheduler", _Fake)
    scheduler = main._start_scheduler()

    assert captured["timezone"] == "America/New_York"
    assert captured["started"] is True
    # and the digest job really is registered at hour 7 in that zone
    digest = [j for j in scheduler.jobs if j.get("id") == "daily-digest"]
    assert digest and digest[0]["hour"] == 7


def test_the_digest_fires_at_seven_local_not_seven_utc(monkeypatch):
    """The whole point, stated as the time an operator would check."""
    _reload(monkeypatch, "America/New_York")
    from apscheduler.triggers.cron import CronTrigger

    fire = CronTrigger(hour=7, minute=0, timezone="America/New_York").get_next_fire_time(
        None, datetime(2026, 8, 8, 0, 0, tzinfo=UTC)
    )
    assert fire.astimezone(UTC).hour == 11  # EDT is UTC-4
    winter = CronTrigger(hour=7, minute=0, timezone="America/New_York").get_next_fire_time(
        None, datetime(2026, 1, 8, 0, 0, tzinfo=UTC)
    )
    # EST is UTC-5 — the same wall-clock hour, a different UTC hour. A fixed
    # offset would have gotten one of these two wrong.
    assert winter.astimezone(UTC).hour == 12


def test_a_fixed_offset_zone_name_is_refused(monkeypatch):
    """tzdata really does carry "EST", and ZoneInfo("EST") resolves — to a
    zone that is UTC-5 all year. A team that writes the abbreviation they say
    out loud would get rituals an hour late for the eight months of DST, with
    /health reporting green."""
    for name in ("EST", "EST5EDT", "MST7MDT"):
        cfg = _reload(monkeypatch, name)
        assert cfg.TZ_NAME == "UTC", f"{name} was accepted"
        assert "fixed offset" in cfg.TZ_ERROR


def test_utc_needs_no_zone_database(monkeypatch):
    """The fallback must not itself require tzdata. ZoneInfo("UTC") is an
    ordinary tzdata lookup, so on a slim image with no /usr/share/zoneinfo it
    raises INSIDE the handler that exists to recover — and a raise at module
    scope means `import app.config` fails and the whole REST API is dead at
    boot, which is the opposite of degrading."""
    cfg = _reload(monkeypatch, "")
    assert cfg.TZ.utcoffset(datetime(2026, 1, 1)) == timedelta(0)
    assert cfg.TZ is UTC or str(cfg.TZ) == "UTC"


def test_a_day_that_starts_without_a_midnight(monkeypatch):
    """Zones whose DST transition lands at 00:00 have no local midnight on the
    spring date. The window must begin at the day's real first instant."""
    _reload(monkeypatch, "America/Santiago")
    start = db.local_midnight_utc(date(2026, 9, 6))  # the transition date
    # 01:00 local is the first moment of that day; anchored to a wall time
    # that never happened, the bound would be an hour off in one direction
    assert datetime.fromisoformat(start).astimezone(ZoneInfo("America/Santiago")).hour == 1


def test_the_offset_is_read_per_date_not_once(monkeypatch):
    """Choosing an IANA name over a fixed offset only pays off if the offset
    is resolved at the date in question. Summer and winter must differ."""
    _reload(monkeypatch, "America/New_York")
    summer = datetime.fromisoformat(db.local_midnight_utc(date(2026, 7, 1)))
    winter = datetime.fromisoformat(db.local_midnight_utc(date(2026, 1, 1)))
    assert summer.hour == 4  # EDT
    assert winter.hour == 5  # EST


def test_a_daily_job_claims_once_per_team_day(monkeypatch):
    """Claim keys are team days now. Two firings inside one local day must
    collapse to one claim, and the day must roll at LOCAL midnight — not at
    20:00 local, which is what a UTC-keyed claim does west of UTC."""
    _reload(monkeypatch, "America/New_York")
    evening = datetime(2026, 8, 8, 23, 0, tzinfo=ZoneInfo("America/New_York"))
    morning = datetime(2026, 8, 8, 9, 0, tzinfo=ZoneInfo("America/New_York"))

    def at(moment):
        class _Fixed(datetime):
            @classmethod
            def now(cls, tz=None):
                return moment.astimezone(tz) if tz else moment

        return _Fixed

    monkeypatch.setattr(db, "datetime", at(morning))
    morning_key = db.today().isoformat()
    monkeypatch.setattr(db, "datetime", at(evening))
    assert db.today().isoformat() == morning_key  # same team day, one claim
    assert db.now()[:10] != morning_key  # and the UTC day has already rolled


def test_an_event_tonight_belongs_to_tonight(monkeypatch):
    """events.starts_at is stored NAIVE UTC (schedule.py::_canon). A local
    date bound against it excludes tonight's events from today's list and
    includes last night's — the failure that motivated local_event_window."""
    _reload(monkeypatch, "America/New_York")
    start, end = db.local_event_window(date(2026, 8, 8))
    tonight = "2026-08-09T00:30"  # 20:30 on the 8th in New York
    last_night = "2026-08-08T00:30"  # 20:30 on the 7th
    assert start <= tonight < end
    assert not (start <= last_night < end)


def test_the_window_includes_an_event_at_exactly_local_midnight(monkeypatch):
    """The shape trap: local_midnight_utc carries an offset suffix, so a naive
    stored value equal to it sorts FIRST as a prefix and drops out of its own
    day. local_event_window matches the column's shape instead."""
    _reload(monkeypatch, "America/New_York")
    start, end = db.local_event_window(date(2026, 8, 8))
    assert start <= "2026-08-08T04:00" < end  # local midnight, in stored shape


def test_local_day_buckets_evening_work_under_the_day_it_was_written(monkeypatch):
    """The standup-chain failure: a bucket keyed on the UTC day can never be
    found by a loop walking local dates, and the team's chain silently resets."""
    _reload(monkeypatch, "America/New_York")
    assert db.local_day("2026-08-09T00:30:00+00:00") == "2026-08-08"
    assert db.local_day("2026-08-09T00:30:00") == "2026-08-08"  # naive is UTC
    assert db.local_day("2026-08-09") == "2026-08-09"  # a date has no zone


def test_east_of_utc_moves_the_day_forward(monkeypatch):
    """Every other test here is west of UTC, where today() lags the UTC day.
    East of it the sign flips, and that is the direction the retention-prune
    month key gets wrong."""
    _reload(monkeypatch, "Asia/Tokyo")
    fixed = datetime(2026, 9, 1, 4, 0, tzinfo=ZoneInfo("Asia/Tokyo"))

    class _Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed

    monkeypatch.setattr(db, "datetime", _Fixed)
    assert db.today().isoformat()[:7] == "2026-09"  # the month the prune claims
    assert db.now()[:7] == "2026-08"  # still August in UTC


def test_health_reports_a_bad_zone_over_http(client, monkeypatch):
    """Reading the fault here is the whole reason it is surfaced."""
    monkeypatch.setattr(config, "TZ_NAME", "UTC")
    monkeypatch.setattr(config, "TZ_ERROR", "SKEIN_TZ=Mars/Base is not an IANA time zone name.")
    body = client.get("/health").json()
    assert body["timezone"] == "UTC"
    assert "IANA" in body["timezone_error"]


def test_health_reports_the_zone(client, monkeypatch):
    monkeypatch.setattr(config, "TZ_NAME", "America/New_York")
    monkeypatch.setattr(config, "TZ_ERROR", "")
    body = client.get("/health").json()
    assert body["timezone"] == "America/New_York"
    assert body["timezone_error"] == ""


def teardown_module():
    """Every other module reads config at import time, so the last reload here
    must leave the suite's own environment in place."""
    importlib.reload(config)


def test_the_404_handler_still_matches_db_notfound(client):
    """A guard against the flake this module caused.

    app/main.py registers its 404 handler against the db.NotFound CLASS
    OBJECT. Anything that reloads app.db mints a new class, the handler stops
    matching, and every NotFound falls through to the ValueError handler as a
    400 — silently, in one xdist worker, for the rest of the run. It cost a
    ~33% flake on tests/test_turn_cost.py before it was found.
    """
    from app import db as db_mod
    from app.main import app

    assert db_mod.NotFound in app.exception_handlers
    # and end to end, which is what actually broke
    assert client.get("/api/tasks/999999").status_code == 404
