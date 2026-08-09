import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

/** The two nav surfaces look alike and do opposite things: search READS,
 *  quick capture WRITES a row into the shared record. A user reported not
 *  being able to tell them apart, so each one now names the other at the
 *  moment the reader wants it — and the capture footer must stay silent for
 *  an anonymous visitor, who has no search box in the nav to be sent to. */

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    // both surfaces are exercised for their EMPTY / idle state
    api: async (path: string) =>
      path.startsWith("/api/ask")
        ? { question: "q", citations: [], note: "" }
        : path.startsWith("/api/search")
          ? []
          : { kind: "note", id: 1 },
  };
});

import { CapturePalette } from "@/components/capture-palette";
import { NavSearch } from "@/components/nav-search";

afterEach(() => window.localStorage.clear());

describe("search and quick capture signpost each other", () => {
  it("teaches quick capture when a search finds nothing", async () => {
    render(<NavSearch />);
    const input = screen.getByLabelText(/Search Skein/);
    fireEvent.change(input, { target: { value: "vendor" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await screen.findByText("Nothing matches those words.");
    // the dead end is the point: a reader who found no record is one
    // keystroke from filing one, and this is the only place outside the nav
    // button that says so
    await waitFor(() =>
      expect(document.body.textContent).toContain("use quick capture"),
    );
    // BOTH spellings are in the markup and globals.css drops the wrong one —
    // a hint that names only ⌘K is the reported bug, since the binding is
    // metaKey OR ctrlKey and most readers are on the ctrl half
    expect(document.body.textContent).toContain("⌘K");
    expect(document.body.textContent).toContain("Ctrl+K");
  });

  it("teaches quick capture when an ask answers with no citations", async () => {
    render(<NavSearch />);
    const input = screen.getByLabelText(/Search Skein/);
    fireEvent.change(input, { target: { value: "?what did we pick" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(document.body.textContent).toContain("use quick capture"),
    );
  });

  it("points a named visitor at search, from the capture footer", () => {
    window.localStorage.setItem("skein-user", "ava");
    render(<CapturePalette />);
    act(() => {
      window.dispatchEvent(new Event("skein-capture-open"));
    });
    expect(document.body.textContent).toContain(
      "To find a record that exists, use the search box in the top bar.",
    );
  });

  it("stays silent about search for an anonymous visitor", () => {
    // nav.tsx renders NavSearch only when a name is picked. Naming a control
    // that is not on screen is worse than naming none.
    render(<CapturePalette />);
    act(() => {
      window.dispatchEvent(new Event("skein-capture-open"));
    });
    expect(screen.getByLabelText("What to capture")).toBeTruthy();
    expect(document.body.textContent).not.toContain("search box in the top bar");
  });
});
