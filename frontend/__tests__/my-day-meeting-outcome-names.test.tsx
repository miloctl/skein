import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** Each meeting row on My Day carries two outcome buttons. With two meetings
 *  waiting, a screen reader's button list read "wrote it up" twice with no
 *  way to tell which meeting each one answers. */

const mocks = vi.hoisted(() => ({ api: vi.fn() }));

// services/briefing.py::_attention, the meeting row
const meeting = (id: number, title: string) => ({
  kind: "meeting",
  ref_id: id,
  group: "notice",
  audience: "you",
  label: `meeting: ${title}`,
  reason: "it ran yesterday at 10:00 and no outcome is recorded",
  link: "/ingest",
});

const briefing = {
  user: "tester",
  date: "2026-08-20",
  attention_total: 2,
  pending_reviews_total: 0,
  attention: [meeting(3, "Weekly sync"), meeting(4, "Vendor call")],
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

describe("the meeting outcome buttons on My Day", () => {
  it("name the meeting each one answers", async () => {
    render(<MyDay />);
    expect(await screen.findByRole("button", { name: "wrote it up: meeting: Weekly sync" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "wrote it up: meeting: Vendor call" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "nothing came out of meeting: Weekly sync" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "nothing came out of meeting: Vendor call" })).toBeTruthy();
  });
});
