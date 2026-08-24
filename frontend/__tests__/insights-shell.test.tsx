import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: () => new Promise(() => {}) };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/insights" }));

import InsightsPage from "@/app/insights/page";

describe("Insights page shell", () => {
  it("keeps the heading and Management view available while loading", () => {
    render(<InsightsPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Insights" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Management view: Off/ })).toBeTruthy();
  });
});
