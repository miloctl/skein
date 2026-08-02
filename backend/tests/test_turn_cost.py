"""Turn cost as a durable record: the thread→engagement link, price-table
cost estimates, the honest unpriced counts, and the budget findings rule.

Costs are ESTIMATES from the operator's price table, computed at write time so
a later price change never rewrites history. No price means cost NULL — never
zero, because zero would silently understate spend."""

import importlib

import pytest

from app import config, db
from app.services import chat_threads, engagements, usage


@pytest.fixture(autouse=True)
def _restore_config():
    yield
    importlib.reload(config)


def _thread(owner: str = "tester", tid: str = "t1") -> str:
    chat_threads.log_message(tid, owner, "user", "hello")
    return tid


def test_prices_parse_and_degrade(monkeypatch):
    monkeypatch.setenv("SKEIN_MODEL_PRICES", '{"m1": [3, 15]}')
    cfg = importlib.reload(config)
    assert cfg.MODEL_PRICES == {"m1": (3.0, 15.0)}
    assert cfg.MODEL_PRICES_ERROR == ""

    monkeypatch.setenv("SKEIN_MODEL_PRICES", '{"m1": "cheap"}')
    cfg = importlib.reload(config)
    assert cfg.MODEL_PRICES == {}
    assert "SKEIN_MODEL_PRICES" in cfg.MODEL_PRICES_ERROR

    monkeypatch.setenv("SKEIN_MODEL_PRICES", '{"m1": [3, -1]}')
    cfg = importlib.reload(config)
    assert cfg.MODEL_PRICES == {}  # a negative price is a typo, not a rebate


def test_a_bad_budget_degrades_and_says_so(monkeypatch):
    monkeypatch.setenv("SKEIN_MONTHLY_BUDGET_USD", "lots")
    cfg = importlib.reload(config)
    assert cfg.MONTHLY_BUDGET_USD == 0.0
    assert "SKEIN_MONTHLY_BUDGET_USD" in cfg.MODEL_PRICES_ERROR


def test_cost_is_computed_at_write_time_and_null_without_a_price(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "MODEL_PRICES", {"priced": (2.0, 10.0)})
    usage.record_chat_usage("t1", "chief-of-staff", "priced", 1_000_000, 100_000)
    usage.record_chat_usage("t1", "chief-of-staff", "mystery", 1_000_000, 100_000)
    rows = db.query("SELECT model_id, cost_usd FROM usage_log ORDER BY id")
    assert rows[0]["cost_usd"] == pytest.approx(2.0 + 1.0)
    assert rows[1]["cost_usd"] is None  # honest, not zero

    # a later price change must not rewrite history
    monkeypatch.setattr(config, "MODEL_PRICES", {"priced": (200.0, 1000.0)})
    assert db.query("SELECT cost_usd FROM usage_log ORDER BY id")[0]["cost_usd"] == pytest.approx(
        3.0
    )


def test_summary_counts_what_the_estimate_cannot_see(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "MODEL_PRICES", {"priced": (1.0, 1.0)})
    usage.record_chat_usage("t1", "a", "priced", 500_000, 500_000)
    usage.record_chat_usage("t1", "a", "mystery", 500_000, 500_000)
    by_model = {r["model_id"]: r for r in usage.usage_summary()}
    assert by_model["priced"]["cost_usd"] == pytest.approx(1.0)
    assert by_model["priced"]["unpriced_calls"] == 0
    assert by_model["mystery"]["cost_usd"] is None
    assert by_model["mystery"]["unpriced_calls"] == 1


def test_linking_a_thread_requires_owning_it_and_a_real_engagement(fresh_db):
    _thread(owner="ava")
    engagements.create_engagement("probe", actor="ava")
    with pytest.raises(ValueError):
        chat_threads.update_thread("t1", "intruder", engagement_id=1)
    with pytest.raises(db.NotFound):
        chat_threads.update_thread("t1", "ava", engagement_id=99)
    row = chat_threads.update_thread("t1", "ava", engagement_id=1)
    assert row["engagement_id"] == 1
    row = chat_threads.update_thread("t1", "ava", engagement_id=0)  # 0 clears
    assert row["engagement_id"] is None


def test_engagement_costs_join_through_the_link(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "MODEL_PRICES", {"m": (1.0, 1.0)})
    engagements.create_engagement("linked one", actor="ava")
    _thread(owner="ava", tid="linked")
    _thread(owner="ava", tid="loose")
    chat_threads.update_thread("linked", "ava", engagement_id=1)
    usage.record_chat_usage("linked", "a", "m", 1_000_000, 0)
    usage.record_chat_usage("loose", "a", "m", 0, 2_000_000)
    usage.record_chat_usage("gone-thread", "a", "mystery", 9, 9)

    rows = {r["engagement"]: r for r in usage.engagement_costs()}
    assert rows["linked one"]["cost_usd"] == pytest.approx(1.0)
    # unlinked and deleted threads land in one honest bucket, not nowhere
    assert rows["(unlinked)"]["cost_usd"] == pytest.approx(2.0)
    assert rows["(unlinked)"]["unpriced_calls"] == 1


def test_month_to_date_reports_budget_and_blind_spots(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "MODEL_PRICES", {"m": (1.0, 1.0)})
    monkeypatch.setattr(config, "MONTHLY_BUDGET_USD", 50.0)
    usage.record_chat_usage("t1", "a", "m", 1_000_000, 0)
    usage.record_chat_usage("t1", "a", "mystery", 1, 1)
    month = usage.month_to_date()
    assert month["cost_usd"] == pytest.approx(1.0)
    assert month["unpriced_calls"] == 1
    assert month["budget_usd"] == 50.0


# ---- the budget findings rule ------------------------------------------------


def test_budget_rule_is_off_without_a_budget(fresh_db, monkeypatch):
    from app.services import insights

    monkeypatch.setattr(config, "MONTHLY_BUDGET_USD", 0.0)
    assert insights._r_budget() == []


def test_budget_rule_fires_at_the_ceiling(fresh_db, monkeypatch):
    from app.services import insights

    monkeypatch.setattr(config, "MODEL_PRICES", {"m": (1.0, 1.0)})
    monkeypatch.setattr(config, "MONTHLY_BUDGET_USD", 2.0)
    usage.record_chat_usage("t1", "a", "m", 1_000_000, 0)
    assert insights._r_budget() == []  # $1 of a $2 budget
    usage.record_chat_usage("t1", "a", "m", 1_000_000, 0)
    fired = insights._r_budget()
    assert len(fired) == 1
    assert fired[0]["rule_id"] == "budget"
    assert fired[0]["severity"] == "high"
    assert "$2.00" in fired[0]["message"]


def test_budget_rule_refuses_to_claim_under_budget_when_it_cannot_measure(fresh_db, monkeypatch):
    """Budget set, calls made, nothing priced: silence would read as 'under
    budget' while nothing was being counted."""
    from app.services import insights

    monkeypatch.setattr(config, "MODEL_PRICES", {})
    monkeypatch.setattr(config, "MONTHLY_BUDGET_USD", 10.0)
    usage.record_chat_usage("t1", "a", "mystery", 1000, 1000)
    fired = insights._r_budget()
    assert len(fired) == 1
    assert "cannot be measured" in fired[0]["message"]
    assert fired[0]["severity"] == "medium"


def test_budget_rule_states_its_blind_spot_when_partially_priced(fresh_db, monkeypatch):
    from app.services import insights

    monkeypatch.setattr(config, "MODEL_PRICES", {"m": (1.0, 1.0)})
    monkeypatch.setattr(config, "MONTHLY_BUDGET_USD", 1.0)
    usage.record_chat_usage("t1", "a", "m", 1_000_000, 0)
    usage.record_chat_usage("t1", "a", "mystery", 1, 1)
    fired = insights._r_budget()
    assert len(fired) == 1
    assert "unpriced" in fired[0]["message"]


# ---- the route and the mistyped-field lesson ---------------------------------


def test_patch_link_via_route_and_reject_typos(client, fresh_db):
    from app.services import engagements as eng

    eng.create_engagement("probe", actor="tester")
    client.post("/api/chat", json={"thread_id": "t-link", "message": "todo: seed the thread"})
    r = client.patch("/api/chats/t-link", json={"engagement_id": 1})
    assert r.status_code == 200
    assert r.json()["engagement_id"] == 1

    r = client.patch("/api/chats/t-link", json={"engagment_id": 0})  # typo'd field
    assert r.status_code == 422  # not a silent no-op

    assert client.patch("/api/chats/t-link", json={"engagement_id": 99}).status_code == 404


def test_usage_endpoint_shape(client, fresh_db):
    body = client.get("/api/usage").json()
    assert set(body) == {"models", "engagements", "month", "prices_error"}
    assert body["month"]["budget_usd"] is None  # off by default
