import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ fail: false }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: () => mocks.fail ? Promise.reject(new Error("Planning unavailable.")) : new Promise(() => {}) };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/planning" }));

import PlanningPage from "@/app/planning/page";

describe("Planning page heading", () => {
  it.each([false, true])("keeps the heading beside Management view when loading fails: %s", async (fail) => {
    mocks.fail = fail;
    try {
      render(<PlanningPage />);
      if (fail) await screen.findByText(/Planning unavailable/);
      expect(screen.getAllByRole("heading", { level: 1, name: "Planning" })).toHaveLength(1);
      const heading = screen.getByRole("heading", { level: 1, name: "Planning" });
      expect(heading.parentElement?.tagName).not.toBe("MAIN");
      expect(heading.parentElement?.contains(screen.getByRole("button", { name: /Management view: Off/ }))).toBe(true);
      expect(screen.queryByRole("navigation", { name: "Section" })).toBeNull();
    } finally { mocks.fail = false; }
  });
});
