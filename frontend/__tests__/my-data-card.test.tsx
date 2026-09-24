import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** What Skein holds that only you can read: counts, a delete for each
 *  private record, and a download of your own data (services/my_data.py). */

const state = vi.hoisted(() => ({
  standups: [
    { id: 7, label: "interview at noon", created_at: "2026-09-20T09:00:00+00:00" },
    { id: 8, label: "dentist", created_at: "2026-09-21T09:00:00+00:00" },
  ],
  calls: [] as { path: string; method?: string }[],
  fetched: [] as string[],
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: { method?: string }) => {
      state.calls.push({ path, method: init?.method });
      if (init?.method === "DELETE") {
        state.standups = state.standups.filter((r) => !path.endsWith(`/${r.id}`));
        return Promise.resolve({ deleted: true });
      }
      if (path === "/api/my-data")
        return Promise.resolve({
          counts: { standups: state.standups.length, tasks: 0, artifacts: 1, solo_chats: 2 },
          file_bytes: 2048,
        });
      if (path === "/api/my-data/standups") return Promise.resolve(state.standups);
      return Promise.resolve([]);
    },
    authenticatedFetch: (path: string) => {
      state.fetched.push(path);
      return Promise.resolve(new Response("{}", { status: 200 }));
    },
  };
});

import { MyDataCard } from "@/components/my-data-card";

beforeEach(() => {
  state.standups = [
    { id: 7, label: "interview at noon", created_at: "2026-09-20T09:00:00+00:00" },
    { id: 8, label: "dentist", created_at: "2026-09-21T09:00:00+00:00" },
  ];
  state.calls = [];
  state.fetched = [];
  URL.createObjectURL = vi.fn(() => "blob:x");
  URL.revokeObjectURL = vi.fn();
});

describe("your data", () => {
  it("counts each kind and says where the others are managed", async () => {
    render(<MyDataCard />);
    expect(await screen.findByText(/Standups:/)).toBeTruthy();
    expect(screen.getByText("Solo chats:", { exact: false }).textContent).toContain("2");
    expect(screen.getByText(/Attached files:/).textContent).toContain("(2 KB)");
    // nothing to show for a kind with no private record
    expect(screen.queryByRole("button", { name: "Show tasks" })).toBeNull();
  });

  it("deletes one private record after a second step", async () => {
    render(<MyDataCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Show standups" }));
    const rows = await screen.findByText("interview at noon");
    const row = within(rows.closest("li")!);
    fireEvent.click(row.getByRole("button", { name: "Delete interview at noon" }));
    expect(state.calls.some((c) => c.method === "DELETE")).toBe(false);
    const confirm = row.getByRole("button", { name: "Delete for good" });
    expect(confirm.getAttribute("aria-describedby")).toBe("my-data-7-consequence");
    fireEvent.click(confirm);
    await waitFor(() =>
      expect(state.calls).toContainEqual({ path: "/api/my-data/standups/7", method: "DELETE" }),
    );
    await waitFor(() => expect(screen.queryByText("interview at noon")).toBeNull());
    expect(screen.getByText("dentist")).toBeTruthy();
    await waitFor(() => expect(document.activeElement?.id).toBe("my-data-intro"));
  });

  it("downloads the export on the authenticated path", async () => {
    render(<MyDataCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Download my data" }));
    await waitFor(() => expect(state.fetched).toEqual(["/api/my-data/export"]));
  });
});
