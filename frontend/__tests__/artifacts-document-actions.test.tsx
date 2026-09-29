import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** Only a document can be changed: a generated report is rewritten by its
 *  generator on its next run, so an edit there would be lost without notice. */

type Row = {
  id: number;
  engagement_id: null;
  kind: string;
  title: string;
  path: string;
  created_by: string;
  created_at: string;
};

const ROWS: Row[] = [
  { id: 12, engagement_id: null, kind: "document", title: "Runbook", path: "/d/12.md", created_by: "scribe", created_at: "2026-09-29T08:00:00+00:00" },
  { id: 7, engagement_id: null, kind: "digest", title: "Digest", path: "/d/7.md", created_by: "scheduler", created_at: "2026-09-29T07:00:00+00:00" },
];
const MADE: Row = { id: 40, engagement_id: null, kind: "document", title: "Plan", path: "/d/40.md", created_by: "ava", created_at: "2026-09-29T09:00:00+00:00" };

const state = vi.hoisted(() => ({ rows: [] as Row[] }));

vi.mock("@/lib/status", () => ({ reportStatus: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/artifacts",
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        state.rows = [MADE, ...state.rows];
        return Promise.resolve({ id: 40, revision: 1, title: "Plan" });
      }
      if (path === "/api/artifacts/page") return Promise.resolve({ items: state.rows, next_before: null });
      if (path.startsWith("/api/crews")) return Promise.resolve([]);
      if (path.endsWith("/revisions")) return Promise.resolve({ id: 12, head: 2, revisions: [] });
      const id = Number(path.split("/").pop());
      const row = [...ROWS, MADE].find((r) => r.id === id);
      return Promise.resolve({ ...row, markdown: `Body of ${id}`, threads: [], revision: 2 });
    },
  };
});

import ArtifactsPage from "@/app/artifacts/page";

beforeEach(() => {
  state.rows = [...ROWS];
  localStorage.clear();
  window.history.replaceState({}, "", "/artifacts?id=12");
});

const text = () => screen.getByLabelText("Document text, Markdown") as HTMLTextAreaElement;

describe("document actions on Reports", () => {
  it("offers Edit and History on a document and opens the editor on its text", async () => {
    render(<ArtifactsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    await waitFor(() => expect(text().value).toBe("Body of 12"));
    expect(screen.getByRole("button", { name: "History" })).toBeTruthy();
  });

  it("offers neither on a generated report", async () => {
    window.history.replaceState({}, "", "/artifacts?id=7");
    render(<ArtifactsPage />);
    await screen.findByText("Body of 7");
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "History" })).toBeNull();
  });

  it("keeps unsaved text across History and back", async () => {
    render(<ArtifactsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    await waitFor(() => expect(text().value).toBe("Body of 12"));
    fireEvent.change(text(), { target: { value: "Body of 12, edited" } });
    fireEvent.click(screen.getByRole("button", { name: "History" }));
    await screen.findByText(/This document has one revision/);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    await waitFor(() => expect(text().value).toBe("Body of 12, edited"));
  });

  it("names the panel each toggle opens", async () => {
    render(<ArtifactsPage />);
    for (const name of ["New document", "Edit", "History"]) {
      const toggle = await screen.findByRole("button", { name });
      expect(toggle.getAttribute("aria-expanded")).toBe("false");
      fireEvent.click(toggle);
      expect(toggle.getAttribute("aria-expanded")).toBe("true");
      expect(document.getElementById(toggle.getAttribute("aria-controls") ?? "")).toBeTruthy();
      fireEvent.click(toggle);
    }
  });

  it("opens the report a search hit names while Reports is open", async () => {
    const view = render(<ArtifactsPage />);
    await screen.findByText("Body of 12");
    // next/link changes the query with no popstate
    act(() => window.history.pushState({}, "", "/artifacts?id=7"));
    view.rerender(<ArtifactsPage />);
    expect(await screen.findByText("Body of 7")).toBeTruthy();
  });

  it("a new document on an empty page replaces the bare entry and focuses Edit", async () => {
    state.rows = [];
    window.history.replaceState({}, "", "/artifacts");
    render(<ArtifactsPage />);
    await screen.findByText(/No report yet/);
    const entries = window.history.length;
    fireEvent.click(screen.getByRole("button", { name: "New document" }));
    fireEvent.change(await screen.findByLabelText("Title"), { target: { value: "Plan" } });
    fireEvent.change(text(), { target: { value: "first draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Create document" }));
    await screen.findByText("Body of 40");
    // pushed, Back returned to a bare /artifacts that opens nothing
    expect(window.history.length).toBe(entries);
    expect(window.location.search).toBe("?id=40");
    await waitFor(() => expect(document.activeElement?.id).toBe("document-edit"));
  });
});
