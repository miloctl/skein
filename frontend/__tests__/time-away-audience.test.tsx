import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ api: vi.fn(), strong: true }));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: mocks.api, getUser: () => "ava" };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));
vi.mock("@/lib/auth", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/auth")>();
  return {
    ...real,
    sessionSnapshot: () => ({ ...real.sessionSnapshot(), strong: mocks.strong }),
  };
});

import Dashboard from "@/app/dashboard/page";

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  mocks.strong = true;
  mocks.api.mockImplementation((path: string, opts?: { method?: string }) => {
    if (opts?.method) return Promise.resolve({ id: 1 });
    if (path === "/api/tasks/browse") return Promise.resolve({ open: [], done: [] });
    if (path === "/api/absences")
      return Promise.resolve([
        {
          id: 7,
          person: "ava",
          kind: "pto",
          starts_on: "2026-08-20",
          ends_on: "2026-08-22",
          note: "",
          visibility: "private",
          dates_shared: false,
          crew_id: 0,
        },
      ]);
    if (path === "/api/pulse") return Promise.resolve(null);
    return Promise.resolve([]);
  });
});

const posted = () =>
  mocks.api.mock.calls
    .filter(([path, opts]) => path === "/api/absences" && opts?.method === "POST")
    .map(([, opts]) => JSON.parse(opts.body));

async function openTimeAway() {
  render(<Dashboard />);
  fireEvent.change(await screen.findByRole("combobox", { name: "Browse register" }), {
    target: { value: "browse-time-away" },
  });
}

function fill(person: string) {
  fireEvent.change(screen.getByRole("combobox", { name: "Who is away" }), {
    target: { value: person },
  });
  fireEvent.change(screen.getByLabelText("Away from"), { target: { value: "2026-09-01" } });
  fireEvent.change(screen.getByLabelText("Away until"), { target: { value: "2026-09-03" } });
}

describe("time away audience", () => {
  it("starts at only me, sends the choice, and remembers it", async () => {
    await openTimeAway();
    const sees = await screen.findByRole("combobox", { name: "Who sees this time away" });
    expect((sees as HTMLSelectElement).value).toBe("nothing");
    fill("ava");
    fireEvent.change(sees, { target: { value: "dates" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(posted()).toHaveLength(1));
    expect(posted()[0]).toMatchObject({ visibility: "private", share_dates: true });
  });

  it("starts a weak identity at the details, which it can read back", async () => {
    // a trusted-header name with no key reads no private row
    mocks.strong = false;
    await openTimeAway();
    const sees = await screen.findByRole("combobox", { name: "Who sees this time away" });
    expect((sees as HTMLSelectElement).value).toBe("details");
  });

  it("keeps the last choice for the next time", async () => {
    localStorage.setItem("skein-audience-absence-ava", JSON.stringify("details"));
    await openTimeAway();
    const sees = await screen.findByRole("combobox", { name: "Who sees this time away" });
    await waitFor(() => expect((sees as HTMLSelectElement).value).toBe("details"));
  });

  it("names no tier for a teammate's window", async () => {
    await openTimeAway();
    await screen.findByRole("combobox", { name: "Who sees this time away" });
    fill("bob");
    expect(screen.queryByRole("combobox", { name: "Who sees this time away" })).toBeNull();
    expect(screen.getByText("Visible to everyone on the roster")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(posted()).toHaveLength(1));
    expect(posted()[0]).not.toHaveProperty("visibility");
  });

  it("widens one of your own windows", async () => {
    await openTimeAway();
    fireEvent.click(await screen.findByRole("button", { name: "share the dates" }));
    await waitFor(() =>
      expect(mocks.api).toHaveBeenCalledWith("/api/absences/7/share", {
        method: "POST",
        body: JSON.stringify({ team_sees: "dates" }),
      }),
    );
  });
});
