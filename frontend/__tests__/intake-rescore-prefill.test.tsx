import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** Re-scoring a request opened on the neutral 3/3/3/3 and discarded the four
 *  numbers already on record, so correcting ONE of them meant remembering and
 *  retyping the other three — against a score the requester can see. The
 *  stored values were on the wire the whole time (`reach`/`impact`/
 *  `confidence`/`effort` in the intake payload) and the panel dropped them.
 *
 *  An UNSCORED row is stored as 0/0/0/1 (migration 001), which the 1-5 inputs
 *  cannot represent — those still open on the 3s. */

const rows = [
  {
    id: 1,
    title: "Scored already",
    detail: "",
    requester: "mira",
    project_class: "prototype",
    reach: 5,
    impact: 4,
    confidence: 2,
    effort: 1,
    score: 40,
    status: "scored",
    disposition_reason: "",
  },
  {
    id: 2,
    title: "Never scored",
    detail: "",
    requester: "dana",
    project_class: "",
    reach: 0,
    impact: 0,
    confidence: 0,
    effort: 1,
    score: 0,
    status: "submitted",
    disposition_reason: "",
  },
];

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: () => Promise.resolve(rows) };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/intake" }));

// the score panel lives behind manager controls
vi.mock("@/components/manage-toggle", () => ({
  ManageToggle: () => null,
  useManageMode: () => true,
}));

import IntakePage from "@/app/intake/page";

const spin = (name: RegExp) => screen.getByRole("spinbutton", { name });

describe("the re-score panel", () => {
  it("opens on the numbers already on record", async () => {
    render(<IntakePage />);
    fireEvent.click((await screen.findAllByText("score…"))[0]);
    expect((spin(/reach/i) as HTMLInputElement).value).toBe("5");
    expect((spin(/impact/i) as HTMLInputElement).value).toBe("4");
    expect((spin(/confidence/i) as HTMLInputElement).value).toBe("2");
    expect((spin(/effort/i) as HTMLInputElement).value).toBe("1");
  });

  it("opens an unscored request on the neutral 3s, never on the stored 0s", async () => {
    render(<IntakePage />);
    fireEvent.click((await screen.findAllByText("score…"))[1]);
    // 0 is what the row stores and what the 1-5 input must never show
    expect((spin(/reach/i) as HTMLInputElement).value).toBe("3");
    expect((spin(/confidence/i) as HTMLInputElement).value).toBe("3");
  });
});
