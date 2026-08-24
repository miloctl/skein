import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: () =>
      Promise.resolve({
        cards: [
          {
            id: "first_watch",
            feature: "First Watch",
            knot: "Bowline on a Coil",
            set: "loops",
            pitch: "Follow one real task.",
            how: "Start the tour.",
            link: "/?tour=first-watch",
            role: "",
            tied: true,
            tied_on: "2026-08-23",
          },
        ],
        newly_tied: [],
        tied_count: 1,
        total: 1,
        known: true,
      }),
  };
});

import GuidePage from "@/app/guide/page";

beforeEach(() => window.localStorage.setItem("skein-user", "tester"));

describe("First Watch Field Guide entry", () => {
  it("keeps replay available after the knot is tied", async () => {
    const starts = vi.fn();
    window.addEventListener("skein-first-watch-start", starts);
    render(<GuidePage />);

    const card = await screen.findByRole("listitem");
    fireEvent.click(
      screen.getByRole("button", { name: "Replay First Watch" }),
    );

    expect(card.textContent).toContain("First Watch");
    expect(starts).toHaveBeenCalledOnce();
    window.removeEventListener("skein-first-watch-start", starts);
  });
});
