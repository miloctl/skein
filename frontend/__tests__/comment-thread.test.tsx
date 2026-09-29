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
  failGet: false,
  holdGet: false,
  failWrite: null as null | Error,
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
        if (state.failWrite) return Promise.reject(state.failWrite);
        if (init.method === "POST")
          return Promise.resolve({ id: 10, parent: "task", parent_id: 12, notified: [], woke: state.woke });
        return Promise.resolve({ id: 9 });
      }
      if (state.holdGet) return new Promise(() => {});
      if (state.failGet) return Promise.reject(new real.ApiError("the database is busy", 503));
      return Promise.resolve(state.rows);
    },
  };
});

import { CommentThread } from "@/components/comment-thread";

beforeEach(() => {
  state.posts = [];
  state.rows = ROWS;
  state.woke = "";
  state.failGet = false;
  state.holdGet = false;
  state.failWrite = null;
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
    const tombstone = await screen.findByText("raj deleted this comment.");
    expect(tombstone.closest("li")?.textContent).not.toContain("wrong guess");
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
    const field = (await screen.findByLabelText(/Add a comment/)) as HTMLTextAreaElement;
    field.focus();
    fireEvent.change(field, { target: { value: "on it" } });
    fireEvent.click(screen.getByRole("button", { name: /Post comment/ }));
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
    const open = screen.getByRole("button", { name: "Delete comment #9" });
    // a double press must not reach the confirm: the second press landed on
    // the same button node, reused as "Delete comment"
    fireEvent.click(open);
    fireEvent.click(open);
    expect(state.posts).toEqual([]);
    expect(screen.getByText("Delete this comment? Skein deletes the text.")).toBeTruthy();
    // the safe choice holds focus, and the confirm names what it asks
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Keep" }));
    const confirm = screen.getByRole("button", { name: "Delete comment" });
    expect(document.getElementById(confirm.getAttribute("aria-describedby") ?? "")?.textContent).toBe(
      "Delete this comment? Skein deletes the text.",
    );
    state.holdGet = true;
    fireEvent.click(confirm);
    await waitFor(() => expect(state.posts).toEqual([{ path: "/api/comments/9", body: null }]));
    // the text leaves the screen at once, before the reload answers
    expect(await screen.findByText(/deleted this comment\./, { selector: "#comment-9 p" })).toBeTruthy();
  });

  it("says so when nobody has written yet", async () => {
    state.rows = [];
    render(<CommentThread parent="decision" id={41} />);
    expect(await screen.findByText("No comments yet. Nobody has pulled on this line.")).toBeTruthy();
  });

  it("tells a sponsor how to ask the delegate, and says when a turn starts", async () => {
    state.woke = "scout";
    render(<CommentThread parent="task" id={12} delegatedAgent="scout" status="in_progress" />);
    expect(await screen.findByText("Write @scout to ask the agent. Skein queues one agent turn.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/Add a comment/), { target: { value: "@scout the target is 17" } });
    fireEvent.click(screen.getByRole("button", { name: /Post comment/ }));
    await waitFor(() =>
      expect(state.reportStatus).toHaveBeenCalledWith(
        "Comment posted. Skein queued one agent turn for scout.",
        "confirmation",
      ),
    );
  });

  it("offers no hint on a finished task", async () => {
    render(<CommentThread parent="task" id={12} delegatedAgent="scout" status="done" />);
    await screen.findByText("Yes, see");
    expect(screen.queryByText(/Skein queues one agent turn/)).toBeNull();
  });

  it("keeps the thread on screen when a reload fails", async () => {
    render(<CommentThread parent="task" id={12} />);
    await screen.findByText("Yes, see");
    state.failGet = true;
    fireEvent.change(screen.getByLabelText(/Add a comment/), { target: { value: "more" } });
    fireEvent.click(screen.getByRole("button", { name: /Post comment/ }));
    await waitFor(() => expect(state.posts).toHaveLength(1));
    await waitFor(() => expect(screen.getByText(/Skein could not read the comments/)).toBeTruthy());
    expect(screen.getByText("Yes, see")).toBeTruthy();
  });

  it("names its own failure to load and to post", async () => {
    state.failGet = true;
    const { unmount } = render(<CommentThread parent="task" id={12} />);
    expect(
      await screen.findByText("Skein could not read the comments. Reload the page to try again."),
    ).toBeTruthy();
    unmount();
    state.failGet = false;
    state.failWrite = new Error("no task #12");
    render(<CommentThread parent="task" id={12} />);
    fireEvent.change(await screen.findByLabelText(/Add a comment/), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: /Post comment/ }));
    await waitFor(() => expect(state.reportStatus).toHaveBeenCalled());
    expect(state.reportStatus.mock.calls[0][0]).toMatch(/^Skein could not post the comment\. /);
  });

  it("Escape cancels an edit and keeps a draft, without closing the panel", async () => {
    render(<CommentThread parent="task" id={12} />);
    await screen.findByText("Yes, see");
    fireEvent.click(screen.getByRole("button", { name: "Edit comment #9" }));
    const edit = screen.getByLabelText("Text of comment #9");
    const escaped = !fireEvent.keyDown(edit, { key: "Escape" });
    expect(escaped).toBe(true);
    expect(screen.queryByLabelText("Text of comment #9")).toBeNull();
    const field = screen.getByLabelText(/Add a comment/);
    fireEvent.change(field, { target: { value: "half a thought" } });
    expect(!fireEvent.keyDown(field, { key: "Escape" })).toBe(true);
    expect((field as HTMLTextAreaElement).value).toBe("half a thought");
  });

  it("asks the panel to reread the task after a post that queues a turn", async () => {
    state.woke = "scout";
    const onPosted = vi.fn();
    render(
      <CommentThread parent="task" id={12} delegatedAgent="scout" status="in_progress" onPosted={onPosted} />,
    );
    fireEvent.change(await screen.findByLabelText(/Add a comment/), { target: { value: "@scout go" } });
    fireEvent.click(screen.getByRole("button", { name: /Post comment/ }));
    await waitFor(() => expect(onPosted).toHaveBeenCalled());
  });
});
