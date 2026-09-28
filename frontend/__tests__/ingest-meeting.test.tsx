import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** Notes pasted from a meeting's own link carry the meeting, so every record
 *  an approval writes links back to it (services/ingest.py::_meeting). */

const bodies: Record<string, unknown>[] = [];

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "ava",
    api: async (path: string, init?: RequestInit) => {
      if (path === "/api/events/5") return { id: 5, title: "Ops review" };
      if (path === "/api/ingest") {
        bodies.push(JSON.parse(String(init?.body)));
        return { proposals: [{ id: 1, kind: "task", line: "todo: x" }], unclassified: ["loose"], skipped_private: 0 };
      }
      return [];
    },
  };
});
vi.mock("next/navigation", () => ({
  usePathname: () => "/ingest",
  useSearchParams: () => new URLSearchParams(window.location.search),
}));

import IngestPage from "@/app/ingest/page";

beforeEach(() => {
  bodies.length = 0;
});

describe("notes pasted for one meeting", () => {
  it("names the meeting and sends it with every paste", async () => {
    window.history.replaceState(null, "", "/ingest?event=5");
    render(<IngestPage />);
    expect(await screen.findByText(/from the meeting "Ops review"/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/Paste your notes/i), { target: { value: "todo: x" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Extract proposals/i }));
    });
    // a line filed by hand from "Not captured" belongs to the same meeting
    fireEvent.change(await screen.findByLabelText('File "loose" as'), { target: { value: "todo:" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "file" }));
    });
    expect(bodies).toEqual([
      { text: "todo: x", event_id: 5 },
      { text: "todo: loose", event_id: 5 },
    ]);
  });

  it("pastes without a meeting once the reader clears it", async () => {
    window.history.replaceState(null, "", "/ingest?event=5");
    render(<IngestPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Paste without a meeting" }));
    fireEvent.change(screen.getByLabelText(/Paste your notes/i), { target: { value: "todo: x" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Extract proposals/i }));
    });
    expect(bodies).toEqual([{ text: "todo: x" }]);
  });
});
