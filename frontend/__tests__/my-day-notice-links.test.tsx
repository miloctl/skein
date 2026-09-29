import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** A notice about a comment links to `?task=12#comment-9`. On My Day it must
 *  open the task panel on that comment: next/link changes the address and
 *  fires nothing the panel listens for (components/notice-link.tsx). */

const mocks = vi.hoisted(() => ({ api: vi.fn() }));

// services/briefing.py::_attention, a notification row
const briefing = {
  user: "tester",
  date: "2026-09-29",
  attention_total: 1,
  pending_reviews_total: 0,
  attention: [
    {
      kind: "notification",
      ref_id: 5,
      group: "notice",
      audience: "you",
      label: "dana commented on task #12.",
      reason: "a reply in a thread you wrote in",
      link: "?task=12#comment-9",
    },
  ],
  your_work: { tasks: [], due_soon: [], standup_suggestion: "" },
  team: { recently_shipped: [], escalated_blockers: [], todays_events: [], recent_activity: [] },
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: mocks.api };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

import MyDay from "@/app/page";

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/");
  window.localStorage.setItem("skein-user", "tester");
  window.localStorage.setItem("skein-onboarded:tester", "1");
  mocks.api.mockImplementation((path: string) => {
    if (path === "/api/briefing") return Promise.resolve(briefing);
    if (path === "/api/onboarding") return Promise.resolve({ steps: [], complete: true, progress: "4/4" });
    if (path.startsWith("/api/field-guide/hint")) return Promise.resolve({ suggestion: null, tied_count: 0, total: 1 });
    if (path.startsWith("/api/delta")) return Promise.resolve({ since: "", quiet: true, items: [] });
    if (path === "/api/users") return Promise.resolve([]);
    return Promise.resolve({});
  });
});

describe("a comment notice on My Day", () => {
  it("opens the task panel on the comment", async () => {
    render(<MyDay />);
    const control = await screen.findByRole("button", { name: /^Open ?dana commented on task #12\.$/ });
    fireEvent.click(control);
    expect(window.location.search).toBe("?task=12");
    expect(window.location.hash).toBe("#comment-9");
  });
});
