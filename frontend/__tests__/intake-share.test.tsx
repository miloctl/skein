import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** A `req:` capture now starts at "only you", and its own page said nothing
 *  about it and offered no way to show it to the triage team. */

const calls = vi.hoisted(() => [] as { path: string; method?: string }[]);
const row = {
  id: 4,
  title: "Need a DB from platform",
  detail: "",
  requester: "ava",
  project_class: "",
  reach: 0,
  impact: 0,
  confidence: 0,
  effort: 1,
  score: 0,
  status: "submitted",
  disposition_reason: "",
  visibility: "private",
  crew_id: 0,
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: { method?: string }) => {
      calls.push({ path, method: init?.method });
      return Promise.resolve(init?.method ? { id: 4 } : [row]);
    },
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/intake" }));

import IntakePage from "@/app/intake/page";

describe("a private request", () => {
  it("shows its audience and shares on purpose", async () => {
    render(<IntakePage />);
    expect(await screen.findByText("only you")).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Share with the team: Need a DB from platform" }),
    );
    await waitFor(() =>
      expect(calls).toContainEqual({ path: "/api/share/requests/4", method: "POST" }),
    );
  });
});
