import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: () => new Promise(() => {}) };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/planning" }));

import PlanningPage from "@/app/planning/page";

describe("Planning page heading", () => {
  it("keeps one level-one heading while data loads", () => {
    render(<PlanningPage />);
    expect(screen.getAllByRole("heading", { level: 1, name: "Planning" })).toHaveLength(1);
  });
});
