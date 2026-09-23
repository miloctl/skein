import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** The slice buttons each start a fetch, and the answers can land in either
 *  order. The list must show the slice that is pressed, never the answer to
 *  the slice the reader has already left. */

const decision = (id: number, title: string, category: string) => ({
  id,
  title,
  decision: `${title} decided`,
  context: "",
  decided_by: "dana",
  review_by: "2026-12-01",
  status: "active",
  category,
  created_at: "2026-09-01T09:00:00+00:00",
});

const answers: { path: string; resolve: (rows: unknown[]) => void }[] = [];
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) =>
      new Promise((resolve) => answers.push({ path, resolve: resolve as (rows: unknown[]) => void })),
  };
});

import CharterPage from "@/app/charter/page";

describe("the charter slice", () => {
  it("ignores the answer for a slice the reader has left", async () => {
    render(<CharterPage />);
    const charterOnly = [decision(1, "Escalation path", "charter")];
    await act(async () => answers.shift()!.resolve(charterOnly));

    fireEvent.click(screen.getByRole("button", { name: "All decisions" }));
    fireEvent.click(screen.getByRole("button", { name: "Charter" }));
    const [all, charter] = answers.splice(0);
    expect(all.path).toBe("/api/decisions");
    expect(charter.path).toBe("/api/decisions?category=charter");

    await act(async () => charter.resolve(charterOnly));
    await act(async () => all.resolve([...charterOnly, decision(2, "Use Postgres", "general")]));

    expect(screen.getByRole("button", { name: "Charter" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("Escalation path")).toBeTruthy();
    expect(screen.queryByText("Use Postgres")).toBeNull();
  });
});
