import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** Pins the null-vs-[] initialization in app/portfolio/page.tsx. Initialized
 *  to [], the conflicts and commitments cards rendered their verdicts
 *  ("Nobody is over 100%") during the first paint and again after a failed
 *  fetch — a confident claim about data that never arrived. The api mock
 *  below never resolves, so this renders the page frozen at first paint. */

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: () => new Promise(() => {}) };
});

vi.mock("next/navigation", () => ({
  usePathname: () => "/portfolio",
}));

import PortfolioPage from "@/app/portfolio/page";

describe("the portfolio page before any data arrives", () => {
  it("says Loading, never a verdict about data it does not have", () => {
    render(<PortfolioPage />);
    expect(screen.getAllByText("Loading…").length).toBeGreaterThan(0);
    expect(screen.queryByText("Nobody is over 100%.")).toBeNull();
    expect(screen.queryByText(/None recorded/)).toBeNull();
  });
});
