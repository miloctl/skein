"""The onboarding checklist: step scoping, progression, and the manager playbook."""


def test_onboarding_checklist_progresses(client):
    first = client.get("/api/onboarding").json()
    assert first["complete"] is False
    by_id = {s["id"]: s["done"] for s in first["steps"]}
    assert by_id["pick_name"] is True  # X-User: tester
    assert by_id["first_capture"] is False

    client.post("/api/capture", json={"text": "todo: onboard me"})
    client.post("/api/engagements", json={"name": "First real work"})
    client.post("/api/standups", json={"today": "getting started"})
    # keys are bootstrapped out-of-band now (python -m app.bootstrap_key)
    from app.services.api_keys import create_key

    create_key("tester", "cli")

    after = client.get("/api/onboarding").json()
    by_id = {s["id"]: s["done"] for s in after["steps"]}
    assert by_id["first_capture"] and by_id["first_engagement"]
    assert by_id["first_standup"] and by_id["setup_key"]
    assert after["next"]["id"] == "invite_team"  # still a team of one


def test_onboarding_scopes_personal_steps_first(client):
    steps = client.get("/api/onboarding").json()["steps"]
    scopes = [s["scope"] for s in steps]
    assert scopes == ["you"] * 4 + ["team"] * 2
    assert steps[0]["id"] == "pick_name"
    assert {s["id"] for s in steps if s["scope"] == "team"} == {"first_engagement", "invite_team"}


# in-page actions app/page.tsx::runStep knows how to perform. A step whose
# link is neither a route nor one of these renders as a dead control.
IN_PAGE_ACTIONS = {"#capture", "#standup"}


def test_onboarding_steps_are_actionable(client, fresh_db):
    steps = client.get("/api/onboarding").json()["steps"]
    assert all(s["hint"] for s in steps)
    for s in steps:
        assert s["link"].startswith("/") or s["link"] in IN_PAGE_ACTIONS, s
        # "/" IS My Day, where the checklist renders: capture and standup
        # pointed there, so the first-run reader clicked the most inviting
        # control on the page and nothing happened
        assert s["link"] != "/", f"{s['id']} links to the page it is shown on"
    assert any(s["id"] == "setup_key" and s["link"] == "/settings" for s in steps)


def test_manager_onboarding_playbook_instantiates(client, fresh_db):
    from app.services.playbooks import instantiate, list_playbooks

    assert any(p["slug"] == "manager_onboarding" for p in list_playbooks())
    created = instantiate("manager_onboarding", "My EM ramp", lead="manager", actor="manager")
    assert len(created["milestones"]) == 4
    assert len(created["events"]) == 2
    titles = [m["title"] for m in created["milestones"]]
    assert any("Listening tour" in t for t in titles)
