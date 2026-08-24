import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ pending: false }));
const cards = [
  {
    id: "capture",
    feature: "Quick capture",
    knot: "Overhand",
    set: "loops",
    pitch: "File work quickly.",
    how: "Use Capture.",
    link: "/",
    role: "",
    tied: false,
    tied_on: "",
  },
  {
    id: "delegate",
    feature: "Delegate to an agent",
    knot: "Rolling hitch",
    set: "hitches",
    pitch: "Hand work to an agent.",
    how: "Open a task.",
    link: "/dashboard",
    role: "",
    tied: true,
    tied_on: "2026-08-24",
  },
  {
    id: "backup",
    feature: "Manual backup",
    knot: "Anchor bend",
    set: "manager",
    pitch: "Take a copy before risk.",
    how: "Open Settings.",
    link: "/settings",
    role: "manager",
    tied: false,
    tied_on: "",
  },
];

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "tester",
    subscribeUser: () => () => {},
    api: () =>
      state.pending
        ? new Promise(() => {})
        : Promise.resolve({
            cards,
            newly_tied: [],
            suggestion: {
              id: "capture",
              feature: "Quick capture",
              pitch: "File work quickly.",
              link: "/",
            },
            tied_count: 1,
            total: 3,
            known: true,
          }),
  };
});

import GuidePage from "@/app/guide/page";

beforeEach(() => {
  state.pending = false;
});

describe("Field Guide discovery", () => {
  it("shows a loading state instead of a blank guide", () => {
    state.pending = true;
    render(<GuidePage />);
    expect(screen.getByText("Loading your Field Guide…")).toBeTruthy();
  });

  it("uses semantic card headings and shows the existing recommendation", async () => {
    render(<GuidePage />);
    expect(
      await screen.findByRole("heading", { level: 2, name: "Recommended next" }),
    ).toBeTruthy();
    expect(screen.getByRole("heading", { level: 3, name: /Quick capture/ })).toBeTruthy();
    expect(screen.getByText("1 feature explored")).toBeTruthy();
  });

  it("filters the loaded guide by search and state", async () => {
    render(<GuidePage />);
    await screen.findByRole("heading", { level: 3, name: /Delegate to an agent/ });
    expect(screen.getByRole("status").textContent).toBe("3 cards shown.");

    fireEvent.change(screen.getByLabelText("Search the Field Guide"), {
      target: { value: "backup" },
    });
    expect(screen.getByText("Manual backup")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("1 card shown.");
    expect(
      screen.queryByRole("heading", { level: 3, name: /Delegate to an agent/ }),
    ).toBeNull();

    fireEvent.change(screen.getByLabelText("Feature state"), {
      target: { value: "tied" },
    });
    expect(screen.getByText("No Field Guide card matches these filters.")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("No cards match.");
  });
});
