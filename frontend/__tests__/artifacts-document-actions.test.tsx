import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** Only a document can be changed: a generated report is rewritten by its
 *  generator on its next run, so an edit there would be lost without notice. */

const ROWS = [
  { id: 12, engagement_id: null, kind: "document", title: "Runbook", path: "/d/12.md", created_by: "scribe", created_at: "2026-09-29T08:00:00+00:00" },
  { id: 7, engagement_id: null, kind: "digest", title: "Digest", path: "/d/7.md", created_by: "scheduler", created_at: "2026-09-29T07:00:00+00:00" },
];

vi.mock("@/lib/status", () => ({ reportStatus: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => {
      if (path === "/api/artifacts/page") return Promise.resolve({ items: ROWS, next_before: null });
      const id = Number(path.split("/").pop());
      const row = ROWS.find((r) => r.id === id);
      return Promise.resolve({ ...row, markdown: `Body of ${id}`, threads: [], revision: 2 });
    },
  };
});

import ArtifactsPage from "@/app/artifacts/page";

beforeEach(() => {
  window.history.replaceState({}, "", "/artifacts?id=12");
});

describe("document actions on Reports", () => {
  it("offers Edit and History on a document and opens the editor on its text", async () => {
    render(<ArtifactsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const area = (await screen.findByLabelText("Document text, Markdown")) as HTMLTextAreaElement;
    await waitFor(() => expect(area.value).toBe("Body of 12"));
    expect(screen.getByRole("button", { name: "History" })).toBeTruthy();
  });

  it("offers neither on a generated report", async () => {
    window.history.replaceState({}, "", "/artifacts?id=7");
    render(<ArtifactsPage />);
    await screen.findByText("Body of 7");
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "History" })).toBeNull();
  });
});
