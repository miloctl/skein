"""The numbers the product shows a team as fact.

docs/INSIGHTS.md states the discipline these pin: medians over means, n
printed everywhere, and person-level data that plans the future but never
judges the past. Each case here was measured against the old code first.
"""

from app.services import stats


def test_median_is_a_median_not_the_upper_middle():
    """flow_metrics used sorted(days)[n // 2], which returns the UPPER of the
    two middle values — a systematically inflated cycle time on every even-n
    window, on the headline number of /portfolio and the exec readout."""
    assert stats.median([1.0, 9.0]) == 5.0
    assert stats.median([1.0, 2.0, 3.0, 4.0]) == 2.5
    assert stats.median([1.0, 2.0, 3.0]) == 2.0
    assert stats.median([5.0]) == 5.0
    assert stats.median([]) is None  # a median of nothing is not zero


def test_p85_is_nearest_rank_including_the_multiple_of_twenty_case():
    """int(0.85n) matches nearest-rank except when 0.85n is exactly an
    integer, where it lands one rank too high."""
    assert stats.p85([float(i) for i in range(1, 21)]) == 17.0  # was 18.0
    assert stats.p85([float(i) for i in range(1, 9)]) == 7.0
    assert stats.p85([3.0]) == 3.0
    assert stats.p85([]) is None


def test_one_median_implementation_shared_by_both_services():
    """The duplicate is how the two disagreed for so long."""
    from app.services import insights, portfolio

    assert insights._median is stats.median
    assert insights._p85 is stats.p85
    assert portfolio._median is stats.median


def test_flow_metrics_reports_the_real_median(fresh_db):
    from app.services import portfolio, work

    a = work.create_task(title="fast", actor="mira")["id"]
    b = work.create_task(title="slow", actor="mira")["id"]
    fresh_db.execute(
        "UPDATE tasks SET completed_at = date('now'), created_at = date('now', '-1 day')"
        " WHERE id = ?",
        (a,),
    )
    fresh_db.execute(
        "UPDATE tasks SET completed_at = date('now'), created_at = date('now', '-9 day')"
        " WHERE id = ?",
        (b,),
    )
    cycle = portfolio.flow_metrics()["cycle_time"]
    assert cycle["tasks_done"] == 2
    assert cycle["median_days"] == 5.0  # not 9.0
    assert cycle["avg_days"] == 5.0


def test_slip_forecast_uses_the_median_so_one_outlier_cannot_move_it(fresh_db):
    """A mean let nine on-time milestones plus one 200 days late push EVERY
    open milestone 20 days. docs/INSIGHTS.md promises medians."""
    from app.services import engagements, portfolio, work

    e = engagements.create_engagement("slip probe", actor="mira")["id"]
    for i in range(9):
        m = work.create_milestone(f"on time {i}", due_date="2026-01-10", actor="mira")["id"]
        fresh_db.execute(
            "UPDATE milestones SET status='done', completed_at='2026-01-10', engagement_id=?"
            " WHERE id=?",
            (e, m),
        )
    late = work.create_milestone("very late", due_date="2026-01-10", actor="mira")["id"]
    fresh_db.execute(
        "UPDATE milestones SET status='done', completed_at='2026-07-29', engagement_id=?"
        " WHERE id=?",
        (e, late),
    )
    open_m = work.create_milestone("upcoming", due_date="2026-09-01", actor="mira")["id"]
    fresh_db.execute("UPDATE milestones SET engagement_id=? WHERE id=?", (e, open_m))

    out = portfolio.slip_forecast()
    assert out["basis"]["median_slip_days"] == 0.0  # the median, not ~20
    row = next(m for m in out["forecasts"] if m["milestone_id"] == open_m)
    assert row["forecast_date"] == "2026-09-01"


def test_insights_returns_no_person_keyed_rows(fresh_db):
    """docs/FEATURES.md: 'no person-keyed insight endpoints exist'. adoption()
    carried active_users — per-person action counts over a PAST window, which
    is the leaderboard input the anti-surveillance rule refuses."""
    from app.services import adoption, insights, users

    users.ensure_user("mira")
    adoption.record_use("mira", "web")

    out = insights.insights()
    assert "active_users" not in out["adoption"]
    assert out["adoption"]["weekly_active_users"] >= 1  # team-rolled facts stay
    # nothing anywhere in the payload names a person
    assert "mira" not in str(out["adoption"])
    # the service keeps it for its own team-rolled computation
    assert "active_users" in adoption.adoption()


def test_the_two_rolling_windows_are_the_same_width(fresh_db):
    """The current window counted back a full WINDOW_DAYS from an inclusive
    today, giving 29 days against the prior window's 28."""
    from datetime import date, timedelta

    from app.services import blockers, insights

    today = date.today()
    for i in range(60):
        b = blockers.raise_blocker(title=f"b{i}", actor="mira")["id"]
        day = (today - timedelta(days=i)).isoformat()
        fresh_db.execute(
            "UPDATE blockers SET status='resolved', created_at=?, resolved_at=? WHERE id=?",
            (day, day, b),
        )
    w = insights.mttr_windows()
    assert w["current"]["n"] == w["previous"]["n"] == w["window_days"]
