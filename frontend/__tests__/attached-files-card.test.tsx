import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** The card exists because a quota with no list is a wall with no door: an
 *  upload is private, so it shows on no other surface, and the refusal that
 *  says "delete an attached file" names nothing the reader can find. */

const mocks = vi.hoisted(() => ({ api: vi.fn() }));

vi.mock("@/lib/api", () => ({
  API_URL: "http://backend.test",
  api: mocks.api,
  bearer: vi.fn(async () => ""),
  userHeader: () => ({ "X-User": "tester" }),
  actionError: (e: unknown) => (e as Error).message,
}));
vi.mock("@/lib/status", () => ({ reportStatus: vi.fn() }));

import { AttachedFilesCard } from "@/components/attached-files-card";

const LISTING = {
  files: [
    { id: 7, title: "roof.md", mime: "text/markdown", size: 2_097_152, created_at: "" },
  ],
  used: 2_097_152,
  quota: 104_857_600,
  max_file: 8_388_608,
};

beforeEach(() => {
  mocks.api.mockReset();
  mocks.api.mockResolvedValue(LISTING);
});

describe("the attached files card", () => {
  it("shows what each file spent and what is left", async () => {
    render(<AttachedFilesCard />);
    expect(await screen.findByText("roof.md")).toBeTruthy();
    // the sentence is the accessible copy; the bar beside it is aria-hidden
    expect(screen.getByText("2.0 MB of 100 MB used")).toBeTruthy();
  });

  it("counts its own files in sentence form", async () => {
    render(<AttachedFilesCard />);
    expect(await screen.findByText(/1 file · 8.0 MB per file/)).toBeTruthy();
  });

  it("sizes a small file in a unit that does not read as empty", async () => {
    // fixed to MB, a note someone attached renders "0.0 MB" and the row looks
    // like a bug rather than the file they can plainly see
    mocks.api.mockResolvedValue({
      ...LISTING,
      files: [{ id: 7, title: "note.md", mime: "text/markdown", size: 35, created_at: "" }],
      used: 35,
    });
    render(<AttachedFilesCard />);
    expect(await screen.findByText("35 bytes")).toBeTruthy();
    expect(screen.getByText("35 bytes of 100 MB used")).toBeTruthy();
  });

  it("asks before it destroys anything", async () => {
    render(<AttachedFilesCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete roof.md" }));
    // the first click only arms it — no request has gone out yet
    expect(mocks.api).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Delete for good" }));
    await waitFor(() =>
      expect(mocks.api).toHaveBeenCalledWith("/api/files/7", { method: "DELETE" }),
    );
  });

  it("leaves the file alone when the confirmation is cancelled", async () => {
    render(<AttachedFilesCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete roof.md" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(mocks.api).not.toHaveBeenCalledWith("/api/files/7", { method: "DELETE" });
    expect(screen.getByRole("button", { name: "Delete roof.md" })).toBeTruthy();
  });

  it("tells the reader where to start when nothing is attached", async () => {
    mocks.api.mockResolvedValue({ ...LISTING, files: [], used: 0 });
    render(<AttachedFilesCard />);
    expect(await screen.findByText(/The \+ in a chat composer adds a file/)).toBeTruthy();
  });

  it("reports a failed delete instead of pretending it worked", async () => {
    mocks.api.mockImplementation(async (path: string, init?: RequestInit) =>
      init?.method === "DELETE"
        ? Promise.reject(new Error("The limit for delete is 20 per minute per person."))
        : LISTING,
    );
    render(<AttachedFilesCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete roof.md" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete for good" }));
    expect(await screen.findByText(/limit for delete/)).toBeTruthy();
    expect(screen.getByText("roof.md")).toBeTruthy();
  });
});
