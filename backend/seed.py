"""Seed realistic demo data. Run once: .venv/bin/python seed.py"""

from datetime import date, timedelta

from app import db
from app.services import blockers, collab, engagements, intake, playbooks, review, users, work


def main() -> None:
    db.init_db()
    if db.query_one("SELECT id FROM engagements LIMIT 1"):
        print("Database already has data — skipping seed.")
        return

    for name in ("mario", "ava", "marcus"):
        users.ensure_user(name)
    for name in ("planner-agent", "research-agent"):
        users.ensure_user(name, kind="agent")

    created = playbooks.instantiate("prototype", "Onboarding revamp",
                                    lead="ava", actor="seed")
    eng_id = created["engagement"]["id"]
    engagements.allocate("ava", eng_id, 60, actor="seed")
    engagements.allocate("marcus", eng_id, 40, actor="seed")

    work.update_task(created["tasks"][0]["id"], status="done", actor="ava")
    work.update_task(created["tasks"][1]["id"], status="in_progress",
                     assignee="marcus", actor="marcus")

    collab.post_standup("ava", yesterday="Interviewed the requester",
                        today="Drafting success criteria",
                        blockers="", actor="ava")
    collab.post_standup("marcus", yesterday="Stack spike",
                        today="Happy-path build",
                        blockers="waiting on staging database access", actor="marcus")

    collab.ask_question("Do we have budget for a usability test round?",
                        asked_by="ava", assigned_to="mario", actor="ava")
    collab.record_decision("Prototype stack", "Next.js + FastAPI, no auth for the demo",
                           context="Fastest path; both already in team toolkit",
                           decided_by="mario", actor="mario")
    collab.save_note("conventions", "All engagements get a handoff package before rotation.",
                     author="mario", actor="mario")

    blockers.raise_blocker("Vendor contract unsigned", owner="mario",
                           impact="high",
                           detail="Blocks the data integration milestone",
                           actor="seed")

    r = intake.submit_request("Diligence on Acme acquisition",
                              detail="Tech due diligence, 2-week window",
                              requester="cfo", project_class="diligence", actor="seed")
    intake.score_request(r["id"], reach=4, impact=5, confidence=3, effort=3, actor="mario")
    r2 = intake.submit_request("Rebuild the wiki", requester="ops", actor="seed")
    intake.score_request(r2["id"], reach=2, impact=2, confidence=4, effort=2, actor="mario")
    intake.disposition_request(r2["id"], "declined",
                               "Low leverage; ops can self-serve with existing tools",
                               actor="mario")

    engagements.record_lesson("Demo with realistic data — stakeholders don't extrapolate",
                              recommendation="Budget half a day for demo data",
                              project_class="prototype", actor="ava")

    review.propose_change(
        "task", "create",
        {"title": "Add error tracking to prototype", "assignee": "marcus",
         "priority": "low"},
        summary="Planner suggests adding error tracking",
        actor="planner-agent",
    )

    from app.services.schedule import schedule_event
    schedule_event("Team sync", f"{date.today().isoformat()}T16:00",
                   attendees="mario,ava,marcus", actor="seed")
    schedule_event("Acme diligence go/no-go",
                   f"{(date.today() + timedelta(days=2)).isoformat()}T11:00",
                   attendees="mario", actor="seed")

    print("Seeded: 1 engagement (playbook), tasks, standups, blockers,"
          " intake queue, pending review, calendar, lessons.")


if __name__ == "__main__":
    main()
