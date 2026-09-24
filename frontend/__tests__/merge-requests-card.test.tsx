import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** A merge that carries private data needs both accounts: this card is the
 *  ask (as the duplicate) and the confirmation (as the account kept). */

const state = vi.hoisted(() => ({
  data: { outgoing: [] as unknown[], incoming: [] as unknown[] },
  posts: [] as { path: string; body?: string }[],
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: { method?: string; body?: string }) => {
      if (init?.method === "POST") {
        state.posts.push({ path, body: init.body });
        return Promise.resolve({ id: 3, status: "pending" });
      }
      return Promise.resolve(state.data);
    },
  };
});

import { MergeRequestsCard } from "@/components/merge-requests-card";

beforeEach(() => {
  state.data = { outgoing: [], incoming: [] };
  state.posts = [];
});

describe("merge requests", () => {
  it("asks to merge this account into another", async () => {
    render(<MergeRequestsCard me="ava2" />);
    fireEvent.change(await screen.findByLabelText("Merge this account into"), {
      target: { value: "ava" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Ask to merge" }));
    await waitFor(() =>
      expect(state.posts).toContainEqual({
        path: "/api/merge-requests",
        body: JSON.stringify({ target: "ava" }),
      }),
    );
  });

  it("states what a confirmation moves before it merges", async () => {
    state.data = {
      outgoing: [],
      incoming: [{ id: 9, source: "ava2", target: "ava", created_at: "2026-09-23" }],
    };
    render(<MergeRequestsCard me="ava" />);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm…" }));
    const merge = screen.getByRole("button", { name: "Merge ava2 into ava" });
    const consequence = document.getElementById(merge.getAttribute("aria-describedby") ?? "");
    expect(consequence?.textContent).toMatch(/private notes, chats, memories and\s+files/);
    expect(consequence?.textContent).toMatch(/You cannot undo this\./);
    expect(state.posts).toHaveLength(0);
    fireEvent.click(merge);
    await waitFor(() =>
      expect(state.posts).toContainEqual({ path: "/api/merge-requests/9/confirm", body: undefined }),
    );
  });
});
