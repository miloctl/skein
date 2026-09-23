import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** A double click on a Health write sends one request. The guard is useless
 *  if the request already left before it runs, or if it reads a busy flag
 *  that only an effect sets, one render after the first click. */

const writes: string[] = [];
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        writes.push(path);
        return new Promise(() => {});
      }
      if (path === "/api/promises")
        return Promise.resolve([
          { id: 7, promise: "Send the pilot report", to_whom: "Acme", due_date: "2026-09-30", status: "open", audience: "external" },
        ]);
      return new Promise(() => {});
    },
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/portfolio" }));
vi.mock("@/components/manage-toggle", () => ({
  ManageToggle: () => null,
  useManageMode: () => true,
}));

import PortfolioPage from "@/app/portfolio/page";

describe("a Health write", () => {
  it("sends one request for a double click", async () => {
    render(<PortfolioPage />);
    const kept = await screen.findByRole("button", { name: "mark kept" });
    act(() => {
      kept.click();
      kept.click();
    });
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes).toEqual(["/api/promises/7/status"]);
  });
});
