import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** A thread on a task, a decision or a blocker. A notice or a pasted link
 *  lands on one comment, a deleted comment keeps its place and names who
 *  deleted it, and a reference links only when the server says the reader
 *  can open it. */

const ROWS = [
  {
    id: 7,
    created_by: "raj",
    origin: "human",
    body: "Is the freeze window fixed? See decision #41.",
    created_at: "2026-09-29T08:00:00+00:00",
    edited_at: "2026-09-29T08:05:00+00:00",
    deleted_at: null,
    deleted_by: "",
    can_edit: false,
    can_delete: false,
    refs: [] as { entity: string; id: number; title?: string }[],
  },
  {
    id: 8,
    created_by: "scout",
    origin: "agent",
    body: "",
    created_at: "2026-09-29T08:10:00+00:00",
    edited_at: null,
    deleted_at: "2026-09-29T08:20:00+00:00",
    deleted_by: "raj",
    can_edit: false,
    can_delete: false,
    refs: [],
  },
  {
    id: 9,
    created_by: "ava",
    origin: "human",
    body: "Yes, see decision #41.",
    created_at: "2026-09-29T09:00:00+00:00",
    edited_at: null,
    deleted_at: null,
    deleted_by: "",
    can_edit: true,
    can_delete: true,
    refs: [{ entity: "decision", id: 41, title: "Freeze Friday" }],
  },
];

const state = vi.hoisted(() => ({
  posts: [] as Array<{ path: string; body: unknown }>,
  rows: [] as unknown[],
  woke: "",
  reportStatus: vi.fn(),
}));

vi.mock("@/lib/status", () => ({ reportStatus: state.reportStatus }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: RequestInit) => {
      if (init?.method) {
        state.posts.push({ path, body: init.body ? JSON.parse(String(init.body)) : null });
        if (init.method === "POST")
          return Promise.resolve({ id: 10, parent: "task", parent_id: 12, notified: [], woke: state.woke });
        return Promise.resolve({ id: 9 });
      }
      return Promise.resolve(state.rows);
    },
  };
});

import { CommentThread } from "@/components/comment-thread";

beforeEach(() => {
  state.posts = [];
  state.rows = ROWS;
  state.woke = "";
  state.reportStatus.mockReset();
  window.history.replaceState({}, "", "/");
});

describe("a comment thread", () => {
  it("lands on the comment a link names once the thread loads", async () => {
    window.history.replaceState({}, "", "/?task=12#comment-9");
    render(<CommentThread parent="task" id={12} />);
    const item = await screen.findByText("Yes, see");
    await waitFor(() => expect(document.activeElement).toBe(item.closest("li")));
  });

  it("names who deleted a comment and shows no text, and marks an edit", async () => {
    render(<CommentThread parent="task" id={12} />);
    expect(await screen.findByText("raj deleted this comment.")).toBeTruthy();
    const edited = document.getElementById("comment-7")!;
    expect(within(edited).getByText("edited")).toBeTruthy();
    const agent = document.getElementById("comment-8")!;
    expect(within(agent).getByText("agent")).toBeTruthy();
  });

  it("links a reference only when the server says the reader can open it", async () => {
    render(<CommentThread parent="task" id={12} />);
    await screen.findByText("Yes, see");
    const links = screen.getAllByRole("link", { name: /decision #41/ });
    expect(links).toHaveLength(1);
    expect(links[0].closest("li")?.id).toBe("comment-9");
    expect(within(document.getElementById("comment-7")!).queryByRole("link")).toBeNull();
  });

  it("posts, clears the field, and keeps focus there", async () => {
    render(<CommentThread parent="blocker" id={4} />);
    const field = (await screen.findByLabelText("Add a comment")) as HTMLTextAreaElement;
    field.focus();
    fireEvent.change(field, { target: { value: "on it" } });
    fireEvent.click(screen.getByRole("button", { name: "Post comment" }));
    await waitFor(() => expect(state.posts).toHaveLength(1));
    expect(state.posts[0]).toEqual({ path: "/api/blockers/4/comments", body: { body: "on it" } });
    await waitFor(() => expect(field.value).toBe(""));
    expect(document.activeElement).toBe(field);
    expect(state.reportStatus).toHaveBeenCalledWith("Comment posted.", "confirmation");
  });

  it("asks before it deletes, and only on the reader's own comment", async () => {
    render(<CommentThread parent="task" id={12} />);
    await screen.findByText("Yes, see");
    expect(screen.getAllByRole("button", { name: /^Delete/ })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Delete comment #9" }));
    expect(screen.getByText("Delete this comment? Skein removes the text.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Delete comment" }));
    await waitFor(() => expect(state.posts).toEqual([{ path: "/api/comments/9", body: null }]));
  });

  it("says so when nobody has written yet", async () => {
    state.rows = [];
    render(<CommentThread parent="decision" id={41} />);
    expect(await screen.findByText("No comments yet. Nobody has pulled on this line.")).toBeTruthy();
  });

  it("tells a sponsor how to ask the delegate, and says when a turn starts", async () => {
    state.woke = "scout";
    render(<CommentThread parent="task" id={12} delegatedAgent="scout" status="in_progress" />);
    expect(await screen.findByText("Write @scout to ask the agent. Skein starts one agent turn.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Add a comment"), { target: { value: "@scout the target is 17" } });
    fireEvent.click(screen.getByRole("button", { name: "Post comment" }));
    await waitFor(() =>
      expect(state.reportStatus).toHaveBeenCalledWith(
        "Comment posted. scout gets one agent turn.",
        "confirmation",
      ),
    );
  });

  it("offers no hint on a finished task", async () => {
    render(<CommentThread parent="task" id={12} delegatedAgent="scout" status="done" />);
    await screen.findByText("Yes, see");
    expect(screen.queryByText(/Skein starts one agent turn/)).toBeNull();
  });
});
