import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** The notes page is where a dump comes back. Before it, the newest 25 notes
 *  were all any surface listed, so an older one was lost unless the reader
 *  remembered its exact words. */

// the row shape GET /api/notes returns (services/collab.py::search_notes)
const note = (id: number) => ({
  id,
  topic: `note ${id}`,
  content: `**body** of note ${id}`,
  author: "ava",
  created_at: "2026-09-27T12:00:00+00:00",
  origin: "human",
  created_by: "ava",
  visibility: "private",
  crew_id: null,
});
const range = (top: number, count: number) =>
  Array.from({ length: count }, (_, i) => note(top - i));

const calls: string[] = [];
let firstPage = range(26, 25);
let failSearch = false;

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "ava",
    api: async (path: string) => {
      calls.push(path);
      if (path === "/api/notes?before=2") return [note(1)];
      // the one-note read: the newest note older than id + 1
      if (path === "/api/notes?before=6&limit=1") return [note(5)];
      if (path === "/api/notes") return firstPage;
      if (path.startsWith("/api/notes?q=")) {
        if (failSearch) throw new Error("search exploded");
        return [];
      }
      return [];
    },
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/notes" }));

import NotesPage from "@/app/notes/page";

beforeEach(() => {
  window.history.replaceState(null, "", "/notes");
  calls.length = 0;
  firstPage = range(26, 25);
  failSearch = false;
});

describe("the Notes page", () => {
  it("pages past the newest 25 with the oldest id it holds", async () => {
    render(<NotesPage />);
    await screen.findByText("note 26");
    expect(screen.queryByText("note 1")).toBeNull();
    fireEvent.click(screen.getByText("Older notes"));
    expect(await screen.findByText("note 1")).toBeTruthy();
    expect(calls).toContain("/api/notes?before=2");
    // a short page is the last one
    expect(screen.queryByText("Older notes")).toBeNull();
  });

  it("opens a note to its full markdown", async () => {
    render(<NotesPage />);
    fireEvent.click(await screen.findByText("note 26"));
    expect(await screen.findByText("body", { selector: "strong" })).toBeTruthy();
    expect(screen.getByLabelText("Edit note: note 26")).toBeTruthy();
  });

  it("adds a new capture on top without losing older pages or an open edit", async () => {
    render(<NotesPage />);
    await screen.findByText("note 26");
    fireEvent.click(screen.getByText("Older notes"));
    fireEvent.click(await screen.findByText("note 1"));
    fireEvent.click(screen.getByLabelText("Edit note: note 1"));
    fireEvent.change(screen.getByLabelText("Note content (markdown)"), {
      target: { value: "half-typed thought" },
    });

    firstPage = range(27, 25);
    act(() => {
      window.dispatchEvent(new Event("skein-attention-change"));
    });

    expect(await screen.findByText("note 27")).toBeTruthy();
    const editor = screen.getByLabelText("Note content (markdown)") as HTMLTextAreaElement;
    expect(editor.value).toBe("half-typed thought");
    expect(screen.getAllByRole("button", { expanded: false })).toHaveLength(26);
  });

  it("shows no earlier rows as the answer to a search that failed", async () => {
    failSearch = true;
    render(<NotesPage />);
    await screen.findByText("note 26");
    fireEvent.change(screen.getByLabelText("Search notes"), { target: { value: "budget" } });
    fireEvent.click(screen.getByText("Search"));
    expect(await screen.findByText(/search exploded/)).toBeTruthy();
    await waitFor(() => expect(screen.queryByText("note 26")).toBeNull());
  });

  it("opens the one note a search hit names, then goes back to the list", async () => {
    window.history.replaceState(null, "", "/notes?note=5");
    render(<NotesPage />);
    expect(await screen.findByText("body", { selector: "strong" })).toBeTruthy();
    expect(screen.getByText("note 5")).toBeTruthy();
    expect(screen.queryByText("note 26")).toBeNull();

    fireEvent.click(screen.getByText("Show all notes"));
    expect(await screen.findByText("note 26")).toBeTruthy();
    expect(window.location.search).toBe("");
  });
});
