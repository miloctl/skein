import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => {
      if (path.includes("/worklog")) return Promise.resolve([]);
      if (path.startsWith("/api/tasks/42"))
        return Promise.resolve({ id: 42, title: "Target task", status: "todo" });
      return Promise.resolve(
        path.includes("%2342")
          ? [
              {
                entity: "task",
                entity_id: 42,
                title: "Target task",
                snippet: "",
              },
            ]
          : [
              {
                entity: "task",
                entity_id: 1,
                title: "Old task",
                snippet: "",
              },
            ],
      );
    },
  };
});

import { NavSearch } from "@/components/nav-search";
import { TaskPeek } from "@/components/task-peek";

describe("First Watch Search contract", () => {
  it("replaces stale results and proves activation through the normal renderer", async () => {
    const receipts: unknown[] = [];
    const onReceipt = (event: Event) => receipts.push((event as CustomEvent).detail);
    window.addEventListener("skein-search-result", onReceipt);
    render(<NavSearch />);
    const input = screen.getByLabelText("Search Skein") as HTMLInputElement;

    fireEvent.change(input, { target: { value: "old" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await screen.findByText("Old task");

    act(() => {
      window.dispatchEvent(new CustomEvent("skein-search-prefill", { detail: "#42" }));
    });

    await waitFor(() => expect(input.value).toBe("#42"));
    expect(screen.queryByText("Old task")).toBeNull();
    expect(document.activeElement).toBe(input);
    expect(input.selectionStart).toBe(0);
    expect(input.selectionEnd).toBe(3);

    fireEvent.keyDown(input, { key: "Enter" });
    const target = await screen.findByRole("button", { name: /Target task/ });
    expect(screen.getByText("1 search result.")).toBeTruthy();
    fireEvent.click(target);

    expect(receipts).toEqual([{ entity: "task", id: 42 }]);
    window.removeEventListener("skein-search-result", onReceipt);
  });

  it("restores Search focus after a result opens Task Peek", async () => {
    window.history.replaceState({}, "", "/");
    render(
      <>
        <NavSearch />
        <TaskPeek />
      </>,
    );
    const input = screen.getByLabelText("Search Skein") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "#42" } });
    fireEvent.keyDown(input, { key: "Enter" });
    const target = await screen.findByRole("button", { name: /Target task/ });
    target.focus();

    fireEvent.click(target);
    await screen.findByRole("dialog", { name: /Target task/ });
    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => expect(document.activeElement).toBe(input));
  });
});
