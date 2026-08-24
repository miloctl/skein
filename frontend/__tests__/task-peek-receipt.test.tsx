import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ fail: false }));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => {
      if (path.includes("worklog")) return Promise.resolve([]);
      if (state.fail) return Promise.reject(new Error("Task not found"));
      return Promise.resolve({ id: 4, title: "Build the path", status: "todo" });
    },
  };
});

import { TaskPeek } from "@/components/task-peek";

beforeEach(() => {
  state.fail = false;
  window.history.replaceState({}, "", "/?task=4");
});

describe("Task Peek result receipts", () => {
  it("reports loaded only after the scoped task read succeeds", async () => {
    const receipts: unknown[] = [];
    const onReceipt = (event: Event) => receipts.push((event as CustomEvent).detail);
    window.addEventListener("skein-peek-result", onReceipt);

    render(<TaskPeek />);

    await screen.findByText("Build the path");
    await waitFor(() =>
      expect(receipts).toEqual([{ taskId: 4, status: "loaded" }]),
    );
    window.removeEventListener("skein-peek-result", onReceipt);
  });

  it("reports one generic unavailable result after a refused read", async () => {
    state.fail = true;
    const receipts: unknown[] = [];
    const onReceipt = (event: Event) => receipts.push((event as CustomEvent).detail);
    window.addEventListener("skein-peek-result", onReceipt);

    render(<TaskPeek />);

    await screen.findByText("Not available");
    await waitFor(() =>
      expect(receipts).toEqual([{ taskId: 4, status: "unavailable" }]),
    );
    window.removeEventListener("skein-peek-result", onReceipt);
  });
});
