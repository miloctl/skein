import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** What Skein holds that only you can read: counts, a delete for each
 *  private record, and a download of your own data (services/my_data.py). */

const state = vi.hoisted(() => ({
  standups: [
    { id: 7, label: "interview at noon", created_at: "2026-09-20T09:00:00+00:00" },
    { id: 8, label: "dentist", created_at: "2026-09-21T09:00:00+00:00" },
  ],
  tasks: [{ id: 7, label: "task seven", created_at: "2026-09-20T09:00:00+00:00" }],
  calls: [] as { path: string; method?: string }[],
  fetched: [] as string[],
  // a DELETE that stays in flight until the test releases it
  hold: null as null | Promise<void>,
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: { method?: string }) => {
      state.calls.push({ path, method: init?.method });
      if (init?.method === "DELETE") {
        const done = () => {
          state.standups = state.standups.filter((r) => !path.endsWith(`/${r.id}`));
          return { deleted: true };
        };
        return state.hold ? state.hold.then(done) : Promise.resolve(done());
      }
      if (path === "/api/my-data")
        return Promise.resolve({
          counts: {
            standups: state.standups.length,
            tasks: state.tasks.length,
            uploads: 1,
            artifacts: 0,
            solo_chats: 2,
          },
          file_bytes: 2048,
        });
      if (path === "/api/my-data/standups") return Promise.resolve(state.standups);
      if (path === "/api/my-data/tasks") return Promise.resolve(state.tasks);
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
  state.tasks = [{ id: 7, label: "task seven", created_at: "2026-09-20T09:00:00+00:00" }];
  state.calls = [];
  state.fetched = [];
  state.hold = null;
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
    expect(screen.queryByRole("button", { name: "Show lessons" })).toBeNull();
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

  it("keeps a late delete out of the list another kind opened", async () => {
    let release!: () => void;
    state.hold = new Promise<void>((r) => {
      release = r;
    });
    render(<MyDataCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Show standups" }));
    const row = within((await screen.findByText("interview at noon")).closest("li")!);
    fireEvent.click(row.getByRole("button", { name: "Delete interview at noon" }));
    fireEvent.click(row.getByRole("button", { name: "Delete for good" }));
    // task #7 shares the id of the standup still being deleted
    fireEvent.click(screen.getByRole("button", { name: "Show tasks" }));
    expect(await screen.findByText("task seven")).toBeTruthy();
    release();
    await waitFor(() => expect(state.standups.map((r) => r.id)).toEqual([8]));
    expect(screen.getByText("task seven")).toBeTruthy();
  });

  it("returns focus to the row's delete button on cancel", async () => {
    render(<MyDataCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Show standups" }));
    const row = within((await screen.findByText("dentist")).closest("li")!);
    fireEvent.click(row.getByRole("button", { name: "Delete dentist" }));
    fireEvent.click(row.getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(document.activeElement?.getAttribute("aria-label")).toBe("Delete dentist"),
    );
  });

  it("keeps the hide button while the last record's list is open", async () => {
    state.standups = [state.standups[0]];
    render(<MyDataCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Show standups" }));
    const row = within((await screen.findByText("interview at noon")).closest("li")!);
    fireEvent.click(row.getByRole("button", { name: "Delete interview at noon" }));
    fireEvent.click(row.getByRole("button", { name: "Delete for good" }));
    expect(await screen.findByText("No private records of this kind are left.")).toBeTruthy();
    await waitFor(() => expect(screen.getByText(/Standups:/).textContent).toContain("0"));
    fireEvent.click(screen.getByRole("button", { name: "Hide standups" }));
    expect(screen.queryByText("No private records of this kind are left.")).toBeNull();
  });
});
