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
      if (path === "/api/briefing") return Promise.resolve(briefing);
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
  window.localStorage.setItem("skein-user", "tester");
  window.localStorage.removeItem("skein-onboarded:tester");
  window.localStorage.removeItem("skein-onboarded:local-user");
  window.localStorage.removeItem("skein-onboarded:resolved-user");
  window.localStorage.removeItem("skein-guided-core-done:tester");
  window.localStorage.removeItem("skein-guided-core-done:resolved-user");
  mocks.onboarding.steps = mocks.onboarding.steps.map((step) => ({
    ...step,
    done: step.id === "pick_name",
  }));
  mocks.onboarding.complete = false;
  mocks.onboardingRequest = null;
});

describe("Guided First Week", () => {
  it("keeps personal work first and discloses team context on request", async () => {
    render(<MyDay />);

    const setup = await screen.findByRole("heading", {
      level: 2,
      name: /Your first-week setup/,
    });
    const needs = screen.getByText("Needs you");
    expect(setup.compareDocumentPosition(needs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("Your work")).toBeTruthy();
    expect(screen.queryByText("Team queues")).toBeNull();
    expect(screen.queryByText("Team today")).toBeNull();
    expect(screen.queryByText("Since yesterday")).toBeNull();

    const toggle = screen.getByRole("button", { name: "Show team context (3 items)" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);

    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Team queues")).toBeTruthy();
    expect(screen.getByText("Team today")).toBeTruthy();
    expect(screen.getByText("Since yesterday")).toBeTruthy();
  });

  it.each(["first_capture", "first_standup"])(
    "keeps guidance active when only %s is complete",
    async (completedStep) => {
      mocks.onboarding.steps = mocks.onboarding.steps.map((step) =>
        step.id === completedStep ? { ...step, done: true } : step,
      );

      render(<MyDay />);

      expect(await screen.findByRole("button", { name: /Show team context/ })).toBeTruthy();
      expect(screen.queryByText("Team queues")).toBeNull();
    },
  );

  it("restores the normal layout after the core personal steps are complete", async () => {
    mocks.onboarding.steps = mocks.onboarding.steps.map((step) =>
      step.id === "first_capture" || step.id === "first_standup"
        ? { ...step, done: true }
        : step,
    );

    render(<MyDay />);

    await waitFor(() => expect(screen.getByText("Team queues")).toBeTruthy());
    expect(screen.getByText("Team today")).toBeTruthy();
    expect(screen.getByText("Since yesterday")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /team context/ })).toBeNull();
  });

  it("keeps team context collapsed while onboarding loads and honors a concurrent dismissal", async () => {
    let resolveOnboarding: (value: typeof mocks.onboarding) => void = () => {};
    mocks.onboardingRequest = new Promise<typeof mocks.onboarding>((resolve) => {
      resolveOnboarding = resolve;
    });

    render(<MyDay />);

    expect(await screen.findByRole("button", { name: /Show team context/ })).toBeTruthy();
    expect(screen.queryByText("Team queues")).toBeNull();

    window.localStorage.setItem("skein-onboarded:tester", "1");
    await act(async () => resolveOnboarding(mocks.onboarding));

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /team context/ })).toBeNull(),
    );
    expect(screen.getByText("Team queues")).toBeTruthy();
  });

  it("never collapses team context for a user this browser saw finish the core steps", async () => {
    // The onboarding read is serial after the briefing, so without the cached
    // verdict this user watched the guided layout flash on every load.
    window.localStorage.setItem("skein-guided-core-done:tester", "1");
    mocks.onboardingRequest = new Promise(() => {}); // never resolves

    render(<MyDay />);

    await waitFor(() => expect(screen.getByText("Team queues")).toBeTruthy());
    expect(screen.getByText("Team today")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /team context/ })).toBeNull();
  });

  it("caches the core-steps verdict once onboarding reports them done", async () => {
    mocks.onboarding.steps = mocks.onboarding.steps.map((step) =>
      step.id === "first_capture" || step.id === "first_standup"
        ? { ...step, done: true }
        : step,
    );

    render(<MyDay />);

    await waitFor(() => expect(screen.getByText("Team queues")).toBeTruthy());
    expect(window.localStorage.getItem("skein-guided-core-done:tester")).toBe("1");
  });

  it("falls open to the full layout when the onboarding read fails", async () => {
    mocks.onboardingRequest = Promise.reject(new Error("onboarding exploded"));

    render(<MyDay />);

    await waitFor(() => expect(screen.getByText("Team queues")).toBeTruthy());
    expect(screen.getByText("Team today")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /team context/ })).toBeNull();
  });

  it("uses the existing dismissal to exit the guided layout", async () => {
    render(<MyDay />);

    expect(await screen.findByRole("button", { name: /Show team context/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss first-week setup" }));

    expect(screen.queryByRole("button", { name: /team context/ })).toBeNull();
    expect(screen.getByText("Team queues")).toBeTruthy();
    expect(screen.getByText("Team today")).toBeTruthy();
    expect(screen.getByText("Since yesterday")).toBeTruthy();
    expect(window.localStorage.getItem("skein-onboarded:tester")).toBe("1");
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("main")));
  });

  it("reads dismissal from the server-resolved identity", async () => {
    briefing.user = "resolved-user";
    window.localStorage.setItem("skein-user", "local-user");
    window.localStorage.setItem("skein-onboarded:resolved-user", "1");
    render(<MyDay />);

    await waitFor(() => expect(screen.getByText("Team queues")).toBeTruthy());
    expect(screen.queryByRole("heading", { name: /Your first-week setup/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /team context/ })).toBeNull();
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
