import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** A decision row on Team → Charter and a blocker row on Work → Browse
 *  carry their thread closed: a page of rows that each fetched a thread on
 *  load is N reads nobody asked for. */

const state = vi.hoisted(() => ({ gets: [] as string[] }));

vi.mock("@/lib/status", () => ({ reportStatus: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => {
      state.gets.push(path);
      return Promise.resolve([]);
    },
  };
});

import { CommentsToggle } from "@/components/comments-toggle";

beforeEach(() => {
  state.gets = [];
});

describe("a closed thread on a row", () => {
  it.each([
    ["decision", 41, "/api/decisions/41/comments"],
    ["blocker", 4, "/api/blockers/4/comments"],
  ] as const)("reads the %s thread only once opened", async (parent, id, path) => {
    render(<CommentsToggle parent={parent} id={id} />);
    const toggle = screen.getByRole("button", { name: `Comments on ${parent} #${id}` });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(state.gets).toEqual([]);
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(document.getElementById(toggle.getAttribute("aria-controls") ?? "")).toBeTruthy();
    expect(await screen.findByText("No comments yet. Nobody has pulled on this line.")).toBeTruthy();
    expect(state.gets).toEqual([path]);
  });
});
