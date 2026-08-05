import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";

/** Pins the null-vs-[] initialization in app/portfolio/page.tsx. Initialized
 *  to [], the conflicts and commitments cards rendered their verdicts
 *  ("Nobody is over 100%") during the first paint and again after a failed
 *  fetch — a confident claim about data that never arrived. The api mock
 *  below never resolves, so this renders the page frozen at first paint. */

const mode = { fail: false };
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: () =>
      mode.fail
        ? Promise.reject(new Error("portfolio service exploded"))
        : new Promise(() => {}),
  };
});

vi.mock("next/navigation", () => ({
  usePathname: () => "/portfolio",
}));

import PortfolioPage from "@/app/portfolio/page";

describe("the portfolio page before any data arrives", () => {
  it("says Loading, never a verdict about data it does not have", async () => {
    const { container } = render(<PortfolioPage />);
    expect(screen.getAllByText("Loading…").length).toBeGreaterThan(0);
    expect(screen.queryByText("Nobody is over 100%.")).toBeNull();
    expect(screen.queryByText(/None recorded/)).toBeNull();
    expect(await axe(container)).toHaveNoViolations();
  });
});

describe("the portfolio page when a card's fetch fails", () => {
  it("says what failed instead of Loading forever", async () => {
    // null means "no data yet" for BOTH not-arrived and failed. A card that
    // only checks null claims work is in progress after the work stopped —
    // and the toast cannot cover it, because six loads share one region.
    mode.fail = true;
    try {
      render(<PortfolioPage />);
      const failures = await screen.findAllByText(/Cannot load .*portfolio service exploded/);
      expect(failures.length).toBeGreaterThan(1); // every failed card, not just one
      expect(screen.queryByText("Loading…")).toBeNull();
    } finally {
      mode.fail = false;
    }
  });
});
