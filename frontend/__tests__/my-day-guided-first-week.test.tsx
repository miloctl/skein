import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  onboarding: {
    steps: [
      { id: "pick_name", label: "Pick your name", done: true, link: "/settings", hint: "", scope: "you" },
      { id: "first_capture", label: "Capture something", done: false, link: "#capture", hint: "", scope: "you" },
      { id: "first_standup", label: "Post a standup", done: false, link: "#standup", hint: "", scope: "you" },
      { id: "setup_key", label: "Set up your personal API key", done: false, link: "/settings", hint: "", scope: "you" },
    ],
    complete: false,
    progress: "1/4",
  },
  onboardingRequest: null as Promise<unknown> | null,
  briefingOverride: null as unknown,
}));

const briefing = {
  user: "tester",
  date: "2026-08-15",
  attention_total: 0,
  pending_reviews_total: 0,
  attention: [
    {
      kind: "intake",
      ref_id: 7,
      group: "decide",
      audience: "team",
      label: "intake #7: Need a thing",
      reason: "awaiting triage",
      link: "/intake",
    },
  ],
  your_work: { tasks: [], due_soon: [], standup_suggestion: "" },
  team: {
    recently_shipped: [],
    escalated_blockers: [{ id: 3, title: "Blocked launch", owner: "tester" }],
    todays_events: [],
    recent_activity: [{ id: 1, actor: "agent", action: "update_task", detail: "#3 done" }],
  },
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => {
      if (path === "/api/briefing") return Promise.resolve(mocks.briefingOverride ?? briefing);
      if (path === "/api/onboarding")
        return mocks.onboardingRequest ?? Promise.resolve(mocks.onboarding);
      if (path.startsWith("/api/field-guide/hint"))
        return Promise.resolve({ suggestion: null, tied_count: 0, total: 1 });
      if (path.startsWith("/api/delta"))
        return Promise.resolve({ since: "", quiet: true, items: [] });
      return Promise.resolve({});
    },
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

import MyDay from "@/app/page";

beforeEach(() => {
  briefing.user = "tester";
  mocks.briefingOverride = null;
  window.localStorage.setItem("skein-user", "tester");
  window.localStorage.removeItem("skein-onboarded:tester");
  window.localStorage.removeItem("skein-onboarded:local-user");
  window.localStorage.removeItem("skein-onboarded:resolved-user");
  mocks.onboarding.steps = mocks.onboarding.steps.map((step) => ({
    ...step,
    done: step.id === "pick_name",
  }));
  mocks.onboarding.complete = false;
  mocks.onboardingRequest = null;
});

describe("Guided First Week", () => {
  it("puts real personal tasks before reporting and counts only hidden context", async () => {
    // Projection of /api/briefing from the disposable seeded mock API as ava.
    mocks.briefingOverride = {"user": "ava", "date": "2026-09-20", "attention_total": 0, "pending_reviews_total": 2, "this_week": "2026-W38", "attention": [{"kind": "proposal", "ref_id": 1, "group": "review", "audience": "team", "label": "proposal #1: Planner suggests adding error tracking", "reason": "proposed by planner-agent — applies only after a human verdict", "link": "/review?id=1"}, {"kind": "proposal", "ref_id": 2, "group": "review", "audience": "team", "label": "proposal #2: accept task #10 'Summarize competitor pricing pages': summary drafted for all 6 competitors", "reason": "proposed by research-agent — applies only after a human verdict", "link": "/review?id=2"}, {"kind": "intake", "ref_id": 1, "group": "decide", "audience": "team", "label": "intake #1: Diligence on Acme acquisition", "reason": "needs an accept, defer, or decline — the requester reads the reason", "link": "/intake"}], "your_work": {"tasks": [{"id": 11, "title": "Check calmer My Day order", "status": "todo", "assignee": "ava", "priority": "medium", "due_date": null, "committed_week": null}], "due_soon": [], "standup_suggestion": "capture task #11; create task #11 Check calmer My Day order; create api key #1 Playwright"}, "team": {"recently_shipped": [], "escalated_blockers": [], "todays_events": [{"id": 1, "title": "Kickoff — Onboarding revamp", "starts_at": "2026-09-20T10:00"}, {"id": 3, "title": "Team sync", "starts_at": "2026-09-20T16:00"}], "recent_activity": [{"id": 55, "actor": "ava", "action": "capture", "detail": "task #11"}, {"id": 54, "actor": "ava", "action": "create_task", "detail": "#11 Check calmer My Day order"}, {"id": 52, "actor": "ava", "action": "create_api_key", "detail": "#1 Playwright"}, {"id": 51, "actor": "ava", "action": "update_crew", "detail": "crew #2 (human)"}, {"id": 50, "actor": "ava", "action": "create_crew", "detail": "crew #2 Launch squad"}, {"id": 49, "actor": "ava", "action": "crew_member_add", "detail": "crew #1 marcus (member)"}, {"id": 48, "actor": "ava", "action": "create_crew", "detail": "crew #1 Platform"}, {"id": 47, "actor": "research-agent", "action": "propose_change", "detail": "#2 update task_completion"}, {"id": 46, "actor": "research-agent", "action": "report_progress", "detail": "task #10 pulled 4 of 6 pricing pages; two need JS rendering"}, {"id": 45, "actor": "research-agent", "action": "claim_task", "detail": "#10 Summarize competitor pricing pages"}, {"id": 39, "actor": "ava", "action": "add_absence", "detail": "#1 ava pto 2026-09-27..2026-10-01"}, {"id": 35, "actor": "planner-agent", "action": "propose_change", "detail": "#1 create task"}]}};
    window.localStorage.setItem("skein-onboarded:ava", "1");
    render(<MyDay />);
    const task = await screen.findByRole("button", { name: "start task #11: Check calmer My Day order" });
    const report = screen.getByText("Post a standup");
    expect(task.compareDocumentPosition(report) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Today's Three" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Show team context (15 items)" }).getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByText("Team sync").closest("[hidden]")).toBeNull();
    const survey = screen.getByRole("button", { name: /Yes — Skein reduced/ });
    expect(survey.closest("section")).toBeNull();
    expect(screen.queryByRole("link", { name: /Set your growth interests/ })).toBeNull();
  });
  it("opens the mounted standup form before onboarding focuses it", async () => {
    render(<MyDay />);
    await screen.findByRole("button", { name: /Post a standup/ });
    const input = document.getElementById("standup-today")!;
    const disclosure = input.closest("details");
    expect(disclosure).not.toBeNull();
    expect(disclosure?.open).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: /Post a standup/ }));
    expect(disclosure?.open).toBe(true);
    expect(document.activeElement).toBe(input);
    fireEvent.change(input, { target: { value: "Keep my update" } });
    fireEvent.click(disclosure!.querySelector("summary")!);
    fireEvent.click(disclosure!.querySelector("summary")!);
    expect((input as HTMLInputElement).value).toBe("Keep my update");
  });

  it("keeps escalations visible while shared queues remain closed", async () => {
    render(<MyDay />);
    await screen.findByRole("heading", { name: "Your work" });
    expect(screen.getByRole("heading", { name: "Team today" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Team queues" })).toBeNull();
  });
  it("offers First Watch from the real-state setup card", async () => {
    const starts = vi.fn();
    window.addEventListener("skein-first-watch-start", starts);
    render(<MyDay />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Start or resume First Watch" }),
    );

    expect(starts).toHaveBeenCalledOnce();
    window.removeEventListener("skein-first-watch-start", starts);
  });

  it("keeps personal work first and discloses team context on request", async () => {
    render(<MyDay />);

    const setup = await screen.findByRole("heading", {
      level: 2,
      name: /Your first-week setup/,
    });
    const needs = screen.getByText("Needs you");
    expect(setup.compareDocumentPosition(needs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("Your work")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Team queues" })).toBeNull();
    expect(screen.getByRole("heading", { name: "Team today" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Since yesterday" })).toBeNull();

    const toggle = screen.getByRole("button", { name: "Show team context (2 items)" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);

    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("heading", { name: "Team queues" })).toBeTruthy();
    expect(screen.getByText("Team today")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Since yesterday" })).toBeTruthy();
  });

  it.each(["first_capture", "first_standup"])(
    "keeps guidance active when only %s is complete",
    async (completedStep) => {
      mocks.onboarding.steps = mocks.onboarding.steps.map((step) =>
        step.id === completedStep ? { ...step, done: true } : step,
      );

      render(<MyDay />);

      expect(await screen.findByRole("button", { name: /Show team context/ })).toBeTruthy();
      expect(screen.queryByRole("heading", { name: "Team queues" })).toBeNull();
    },
  );

  it("keeps shared context closed after the core personal steps are complete", async () => {
    mocks.onboarding.steps = mocks.onboarding.steps.map((step) =>
      step.id === "first_capture" || step.id === "first_standup"
        ? { ...step, done: true }
        : step,
    );

    render(<MyDay />);

    await screen.findByRole("heading", { name: "Team today" });
    expect(screen.queryByRole("heading", { name: "Team queues" })).toBeNull();
    expect(screen.getByText("Team today")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Since yesterday" })).toBeNull();
    expect(screen.getByRole("button", { name: /Show team context/ }).getAttribute("aria-expanded")).toBe("false");
  });

  it("keeps team context collapsed while onboarding loads and honors a concurrent dismissal", async () => {
    let resolveOnboarding: (value: typeof mocks.onboarding) => void = () => {};
    mocks.onboardingRequest = new Promise<typeof mocks.onboarding>((resolve) => {
      resolveOnboarding = resolve;
    });

    render(<MyDay />);

    expect(await screen.findByRole("button", { name: /Show team context/ })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Team queues" })).toBeNull();

    window.localStorage.setItem("skein-onboarded:tester", "1");
    await act(async () => resolveOnboarding(mocks.onboarding));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Show team context/ }).getAttribute("aria-expanded")).toBe("false"),
    );
    expect(screen.queryByRole("heading", { name: "Team queues" })).toBeNull();
  });

  it("keeps shared context closed for an established user while onboarding loads", async () => {
    // The onboarding read is serial after the briefing, so without the cached
    // verdict this user watched the guided layout flash on every load.
    mocks.onboardingRequest = new Promise(() => {}); // never resolves

    render(<MyDay />);

    await screen.findByRole("heading", { name: "Team today" });
    expect(screen.queryByRole("heading", { name: "Team queues" })).toBeNull();
    expect(screen.getByText("Team today")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Show team context/ }).getAttribute("aria-expanded")).toBe("false");
  });

  it("keeps shared context closed when onboarding reports the core steps done", async () => {
    mocks.onboarding.steps = mocks.onboarding.steps.map((step) =>
      step.id === "first_capture" || step.id === "first_standup"
        ? { ...step, done: true }
        : step,
    );

    render(<MyDay />);

    await screen.findByRole("heading", { name: "Team today" });
    expect(screen.queryByRole("heading", { name: "Team queues" })).toBeNull();
    expect(screen.getByRole("button", { name: /Show team context/ }).getAttribute("aria-expanded")).toBe("false");
  });

  it("keeps work and urgent context visible when onboarding fails", async () => {
    mocks.onboardingRequest = Promise.reject(new Error("onboarding exploded"));

    render(<MyDay />);

    await screen.findByRole("heading", { name: "Team today" });
    expect(screen.queryByRole("heading", { name: "Team queues" })).toBeNull();
    expect(screen.getByText("Team today")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Show team context/ }).getAttribute("aria-expanded")).toBe("false");
  });

  it("dismisses guidance without expanding shared context", async () => {
    render(<MyDay />);

    expect(await screen.findByRole("button", { name: /Show team context/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss first-week setup" }));

    expect(screen.getByRole("button", { name: /Show team context/ }).getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("heading", { name: "Team queues" })).toBeNull();
    expect(screen.getByText("Team today")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Since yesterday" })).toBeNull();
    expect(window.localStorage.getItem("skein-onboarded:tester")).toBe("1");
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("main")));
  });

  it("reads dismissal from the server-resolved identity", async () => {
    briefing.user = "resolved-user";
    window.localStorage.setItem("skein-user", "local-user");
    window.localStorage.setItem("skein-onboarded:resolved-user", "1");
    render(<MyDay />);

    await screen.findByRole("heading", { name: "Team today" });
    expect(screen.queryByRole("heading", { name: "Team queues" })).toBeNull();
    expect(screen.queryByRole("heading", { name: /Your first-week setup/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Show team context/ }).getAttribute("aria-expanded")).toBe("false");
  });

  it("keys dismissal to the server-resolved identity", async () => {
    briefing.user = "resolved-user";
    window.localStorage.setItem("skein-user", "local-user");
    render(<MyDay />);

    await screen.findByRole("button", { name: /Show team context/ });
    fireEvent.click(screen.getByRole("button", { name: "Dismiss first-week setup" }));

    expect(window.localStorage.getItem("skein-onboarded:resolved-user")).toBe("1");
    expect(window.localStorage.getItem("skein-onboarded:local-user")).toBeNull();
  });
});
