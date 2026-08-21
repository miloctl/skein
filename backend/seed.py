"""Seed realistic demo data. Run once: .venv/bin/python seed.py"""

from datetime import date, timedelta

from app import db
from app.services import (
    blockers,
    collab,
    crews,
    engagements,
    intake,
    playbooks,
    review,
    users,
    work,
)


def main() -> None:
    db.init_db()
    if db.query_one("SELECT id FROM engagements LIMIT 1"):
        print("Database already has data — skipping seed.")
        return

    for name in ("mario", "ava", "marcus"):
        users.ensure_user(name)
    for name in ("planner-agent", "research-agent"):
        users.ensure_user(name, kind="agent")

    created = playbooks.instantiate("prototype", "Onboarding revamp", lead="ava", actor="seed")
    eng_id = created["engagement"]["id"]
    engagements.allocate("ava", eng_id, 60, actor="seed")
    engagements.allocate("marcus", eng_id, 40, actor="seed")

    work.update_task(created["tasks"][0]["id"], status="done", actor="ava")
    work.update_task(
        created["tasks"][1]["id"], status="in_progress", assignee="marcus", actor="marcus"
    )

    collab.post_standup(
        "ava",
        yesterday="Interviewed the requester",
        today="Drafting success criteria",
        blockers="",
        actor="ava",
    )
    collab.post_standup(
        "marcus",
        yesterday="Stack spike",
        today="Happy-path build",
        blockers="waiting on staging database access",
        actor="marcus",
    )

    collab.ask_question(
        "Do we have budget for a usability test round?",
        asked_by="ava",
        assigned_to="mario",
        actor="ava",
    )
    collab.record_decision(
        "Prototype stack",
        "Next.js + FastAPI, no auth for the demo",
        context="Fastest path; both already in team toolkit",
        decided_by="mario",
        actor="mario",
    )
    collab.save_note(
        "conventions",
        "All engagements get a handoff package before rotation.",
        author="mario",
        actor="mario",
    )

    blockers.raise_blocker(
        "Vendor contract unsigned",
        owner="mario",
        impact="high",
        detail="Blocks the data integration milestone",
        actor="seed",
    )

    r = intake.submit_request(
        "Diligence on Acme acquisition",
        detail="Tech due diligence, 2-week window",
        requester="cfo",
        project_class="diligence",
        actor="seed",
    )
    intake.score_request(r["id"], reach=4, impact=5, confidence=3, effort=3, actor="mario")
    r2 = intake.submit_request("Rebuild the wiki", requester="ops", actor="seed")
    intake.score_request(r2["id"], reach=2, impact=2, confidence=4, effort=2, actor="mario")
    intake.disposition_request(
        r2["id"],
        "declined",
        "Low leverage; ops can self-serve with existing tools",
        actor="mario",
    )

    engagements.record_lesson(
        "Demo with realistic data — stakeholders don't extrapolate",
        recommendation="Budget half a day for demo data",
        project_class="prototype",
        actor="ava",
    )

    review.propose_change(
        "task",
        "create",
        {"title": "Add error tracking to prototype", "assignee": "marcus", "priority": "low"},
        summary="Planner suggests adding error tracking",
        actor="planner-agent",
    )

    from app.services.schedule import schedule_event

    schedule_event(
        "Team sync",
        f"{date.today().isoformat()}T16:00",
        attendees="mario,ava,marcus",
        actor="seed",
    )
    schedule_event(
        "Acme diligence go/no-go",
        f"{(date.today() + timedelta(days=2)).isoformat()}T11:00",
        attendees="mario",
        actor="seed",
    )

    # ---- flagship flows the demo must show: promise, absence, delegation ----
    from app.services import absences, delegation, promises

    promises.add_promise(
        "Send Acme the diligence summary",
        to_whom="Acme PM",
        due_date=(date.today() + timedelta(days=3)).isoformat(),
        actor="mario",
    )
    absences.add_absence(
        "ava",
        (date.today() + timedelta(days=7)).isoformat(),
        (date.today() + timedelta(days=11)).isoformat(),
        kind="pto",
        note="offsite week",
        actor="ava",
    )
    collab.record_decision(
        "Weekly plan is approved, never imposed",
        "The Monday draft ships as a proposal; a human approves the commitment line.",
        decided_by="mario",
        review_by=(date.today() + timedelta(days=60)).isoformat(),
        category="charter",
        actor="mario",
    )
    # The gate ships ON (config.AGENT_REVIEW), and a demo where every chat
    # capture queues for approval reads as friction, not governance. These
    # grants are the deliberate deployment choice the authority page teaches:
    # the built-in chat agent writes the two capture staples directly, and
    # every other (agent, entity) pair stays at review — so the inbox still
    # shows the loop, and the matrix card has real rows on first load.
    # the startup hook reserves the core "agent" identity before any request;
    # seed runs outside the app, so it makes the same reservation itself
    # (set_authority refuses to MINT a core machine subject from text)
    from app.services.users import _reserve_core_agent_identity

    _reserve_core_agent_identity("agent")
    delegation.set_authority("agent", "task", "autonomous", actor="mario")
    delegation.set_authority("agent", "note", "autonomous", actor="mario")

    # a delegation mid-loop: claimed, one worklog entry, awaiting acceptance
    dt = work.create_task(
        title="Summarize competitor pricing pages", assignee="research-agent", actor="mario"
    )
    delegation.delegate_task(dt["id"], "research-agent", "mario", actor="mario")
    delegation.claim_task(dt["id"], actor="research-agent")
    delegation.report_progress(
        dt["id"], "pulled 4 of 6 pricing pages; two need JS rendering", actor="research-agent"
    )
    delegation.submit_completion(
        dt["id"], "summary drafted for all 6 competitors", actor="research-agent"
    )

    # One flock turn, so /agents renders the DIAMOND rather than its empty
    # state. Without a trace the e2e walk scans the empty branch and the
    # component — its aria-label, its text nodes, its strokes in dark — is
    # never seen by axe at all.
    from app.services import flocks

    eng = flocks.get_flock("engineering")
    flocks.record_trace(
        "seed-flock",
        "mario",
        eng["slug"],
        [
            {
                "slug": card["slug"],
                "name": card["name"],
                "emoji": card["emoji"],
                "status": status,
                "ms": ms,
                "receipts": receipts,
                "tokens_in": 0,
                "tokens_out": 0,
            }
            # one of each status: the diamond colours and words them
            # differently, and only a mixed row exercises all three
            for card, status, ms, receipts in zip(
                flocks.member_cards(eng["members"]),
                ("ok", "ok", "failed"),
                (5834, 7524, 1102),
                (0, 1, 0),
                strict=True,
            )
        ],
    )

    # two crews, one of them deactivated: that branch is a different render
    # path (dashed border, "inactive" badge) and the e2e axe walk is the only
    # thing that sees it
    platform = crews.create_crew("Platform", summary="the backend half", actor="ava")
    crews.add_member(platform["id"], "marcus", actor="ava")
    crews.update_crew(
        crews.create_crew("Launch squad", actor="ava")["id"], active=False, actor="ava"
    )

    print(
        "Seeded: 1 engagement (playbook), tasks, standups, blockers,"
        " intake queue, pending reviews, calendar, lessons, a promise,"
        " an absence, a charter entry, a delegation awaiting acceptance,"
        " one flock turn, and two crews (one deactivated)."
    )


if __name__ == "__main__":
    main()
