import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** A person edits a document as its next revision. The save names the
 *  revision it started from, so a second person saving at once gets a 409
 *  and keeps the text they typed. */

const state = vi.hoisted(() => ({
  puts: [] as unknown[],
  posts: [] as unknown[],
  conflict: false,
  strong: true,
  reportStatus: vi.fn(),
}));

vi.mock("@/lib/status", () => ({ reportStatus: state.reportStatus }));
vi.mock("@/lib/audience", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/audience")>();
  return { ...real, useStrongIdentity: () => state.strong };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        state.posts.push(JSON.parse(String(init.body)));
        return Promise.resolve({ id: 40, revision: 1, title: "Runbook" });
      }
      if (init?.method === "PUT") {
        state.puts.push(JSON.parse(String(init.body)));
        return state.conflict
          ? Promise.reject(
              new real.ApiError(
                "document #12 changed after revision 5, and revision 6 is newer. Read revision 6, then make the change again.",
                409,
              ),
            )
          : Promise.resolve({ id: 12, revision: 6, unchanged: false });
      }
      return Promise.resolve({ id: 12, markdown: "alpha", revision: 5 });
    },
  };
});

import { DocumentEditor } from "@/components/document-editor";

beforeEach(() => {
  state.puts = [];
  state.posts = [];
  state.strong = true;
  localStorage.clear();
  state.conflict = false;
  state.reportStatus.mockReset();
});

const text = () => screen.getByLabelText("Document text, Markdown") as HTMLTextAreaElement;

describe("the document editor", () => {
  it("keeps the text and shows the reason when the save answers 409", async () => {
    state.conflict = true;
    const onSaved = vi.fn();
    render(<DocumentEditor artifactId={12} onSaved={onSaved} onCancel={() => {}} />);
    await waitFor(() => expect(text().value).toBe("alpha"));
    fireEvent.change(text(), { target: { value: "alpha beta" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(state.reportStatus).toHaveBeenCalled());
    expect(state.puts).toEqual([{ content: "alpha beta", base_revision: 5 }]);
    expect(state.reportStatus.mock.calls[0][0]).toContain("revision 6 is newer");
    expect(text().value).toBe("alpha beta");
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("saves as the next revision and says which", async () => {
    const onSaved = vi.fn();
    render(<DocumentEditor artifactId={12} onSaved={onSaved} onCancel={() => {}} />);
    await waitFor(() => expect(text().value).toBe("alpha"));
    fireEvent.change(text(), { target: { value: "alpha gamma" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(state.reportStatus).toHaveBeenCalledWith("Saved revision 6.", "confirmation");
  });

  it("Preview keeps the textarea mounted", async () => {
    render(<DocumentEditor artifactId={12} onSaved={() => {}} onCancel={() => {}} />);
    await waitFor(() => expect(text().value).toBe("alpha"));
    fireEvent.change(text(), { target: { value: "# Heading drafted" } });
    const area = text();
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(screen.getByRole("heading", { name: "Heading drafted" })).toBeTruthy();
    expect(area.isConnected).toBe(true);
    expect(area.hidden).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Write" }));
    expect(text()).toBe(area);
    expect(area.value).toBe("# Heading drafted");
  });
});

describe("a new document", () => {
  it.each([
    [true, "private"],
    [false, "workspace"],
  ])(
    "starts at the narrowest audience its author can open (signed in: %s)",
    async (strong, visibility) => {
      state.strong = strong;
      const onSaved = vi.fn();
      render(<DocumentEditor artifactId={null} onSaved={onSaved} onCancel={() => {}} />);
      fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Runbook" } });
      fireEvent.change(text(), { target: { value: "first draft" } });
      fireEvent.click(screen.getByRole("button", { name: "Create document" }));
      await waitFor(() => expect(onSaved).toHaveBeenCalledWith(40));
      expect(state.posts).toEqual([
        { title: "Runbook", content: "first draft", visibility, crew_id: 0 },
      ]);
      expect(state.reportStatus).toHaveBeenCalledWith("Created document #40.", "confirmation");
    },
  );
});
