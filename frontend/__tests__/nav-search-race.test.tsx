import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  handler: null as null | ((path: string) => Promise<unknown>),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => state.handler?.(path) ?? Promise.resolve([]),
  };
});

import { NavSearch } from "@/components/nav-search";

function deferred<T>() {
  let resolve: (value: T) => void = () => {};
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

beforeEach(() => {
  state.handler = null;
});

describe("Nav Search request ordering", () => {
  it("ignores an old request that resolves after a First Watch prefill search", async () => {
    const old = deferred<unknown>();
    const current = deferred<unknown>();
    state.handler = (path) => (path.includes("old") ? old.promise : current.promise);
    render(<NavSearch />);
    const input = screen.getByLabelText("Search Skein") as HTMLInputElement;

    fireEvent.change(input, { target: { value: "old" } });
    fireEvent.keyDown(input, { key: "Enter" });
    act(() => {
      window.dispatchEvent(new CustomEvent("skein-search-prefill", { detail: "#42" }));
    });
    fireEvent.keyDown(input, { key: "Enter" });

    await act(async () => {
      current.resolve([
        { entity: "task", entity_id: 42, title: "Current task", snippet: "" },
      ]);
      await current.promise;
    });
    expect(screen.getByText("Current task")).toBeTruthy();

    await act(async () => {
      old.resolve([{ entity: "task", entity_id: 1, title: "Old task", snippet: "" }]);
      await old.promise;
    });
    expect(screen.getByText("Current task")).toBeTruthy();
    expect(screen.queryByText("Old task")).toBeNull();
  });
});
