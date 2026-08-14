import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** The capacity projection on the accept panel is computed FROM the chips
 *  and the percent, so it stops being an answer the moment either changes.
 *  Left standing it reads as a projection of what is on screen now: a
 *  triager who raised 50% to 80% saw the number for 50 and accepted on it.
 *  These pin the invalidation, the roster filter, and the percent clamp. */

const mocks = vi.hoisted(() => ({
  users: vi.fn(),
  whatIf: vi.fn(),
}));

const scored = {
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
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, opts?: { method?: string }) => {
      if (path === "/api/users") return mocks.users();
      if (path.includes("what-if")) return mocks.whatIf(opts);
      return Promise.resolve([scored]);
    },
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/intake" }));
vi.mock("@/components/manage-toggle", () => ({
  ManageToggle: () => null,
  useManageMode: () => true,
}));

import IntakePage from "@/app/intake/page";

const projection = (person: string, projected: number) => ({
  assumed_percent: 50,
  projection: [
    {
      person,
      current_percent: 40,
      projected_percent: projected,
      overcommitted: false,
      growth_interests: "",
      upcoming_absence: "",
    },
  ],
});

async function openAcceptPanel() {
  render(<IntakePage />);
  fireEvent.click(await screen.findByText("accept…"));
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.users.mockResolvedValue([
    { name: "ava", kind: "human", active: 1 },
    { name: "scout", kind: "agent", active: 1 },
    { name: "departed", kind: "human", active: 0 },
  ]);
  mocks.whatIf.mockResolvedValue(projection("ava", 90));
});

describe("who the projection can be run against", () => {
  it("offers active people only, never agents or departed teammates", async () => {
    await openAcceptPanel();
    expect(await screen.findByRole("button", { name: "ava" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "scout" })).toBeNull();
    expect(screen.queryByRole("button", { name: "departed" })).toBeNull();
  });

  it("says the roster failed to load instead of showing an empty team", async () => {
    // an empty chip row is the same sentence as a real empty roster, and
    // this card exists to ask a capacity question
    mocks.users.mockRejectedValue(new Error("roster store exploded"));
    await openAcceptPanel();
    expect(await screen.findByText(/roster store exploded/)).toBeTruthy();
    expect(screen.queryByText(/No active teammate/)).toBeNull();
  });

  it("distinguishes a genuinely empty roster from a failed one", async () => {
    mocks.users.mockResolvedValue([]);
    await openAcceptPanel();
    expect(await screen.findByText(/No active teammate is on the roster/)).toBeTruthy();
  });
});

describe("a projection stops being an answer when its inputs change", () => {
  async function runProjection() {
    await openAcceptPanel();
    fireEvent.click(await screen.findByRole("button", { name: "ava" }));
    fireEvent.click(screen.getByRole("button", { name: "project" }));
    return await screen.findByText(/90/);
  }

  it("clears the projection when the people change", async () => {
    await runProjection();
    fireEvent.click(screen.getByRole("button", { name: "ava" })); // deselect
    await waitFor(() => expect(screen.queryByText(/90/)).toBeNull());
  });

  it("clears the projection when the assumed percent changes", async () => {
    await runProjection();
    fireEvent.change(screen.getByLabelText("Percent of each person"), {
      target: { value: "80" },
    });
    await waitFor(() => expect(screen.queryByText(/90/)).toBeNull());
  });
});

describe("the assumed percent stays inside what the service accepts", () => {
  it("clamps an emptied field to 1 rather than sending 0", async () => {
    // Number("") is 0, which the service refuses with a 400 for a value the
    // reader never chose
    await openAcceptPanel();
    const pct = (await screen.findByLabelText(
      "Percent of each person",
    )) as HTMLInputElement;
    fireEvent.change(pct, { target: { value: "" } });
    expect(pct.value).toBe("1");
  });

  it("clamps above 100 back to 100", async () => {
    await openAcceptPanel();
    const pct = (await screen.findByLabelText(
      "Percent of each person",
    )) as HTMLInputElement;
    fireEvent.change(pct, { target: { value: "400" } });
    expect(pct.value).toBe("100");
  });

  it("sends the picked people and the clamped percent", async () => {
    await openAcceptPanel();
    fireEvent.click(await screen.findByRole("button", { name: "ava" }));
    fireEvent.change(screen.getByLabelText("Percent of each person"), {
      target: { value: "70" },
    });
    fireEvent.click(screen.getByRole("button", { name: "project" }));
    await waitFor(() => expect(mocks.whatIf).toHaveBeenCalled());
    expect(JSON.parse(mocks.whatIf.mock.calls[0][0].body)).toEqual({
      people: ["ava"],
      percent: 70,
    });
  });
});
