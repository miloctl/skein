import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  roster: "data" as "data" | "empty" | "failed" | "pending",
}));

const roster = [
  { name: "operator", kind: "human", active: 1 },
  { name: "Ava", kind: "human", active: 1 },
  { name: "Bo", kind: "human", active: 1 },
  { name: "Dana", kind: "human", active: 0 },
];

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "operator",
    api: mocks.api,
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/settings" }));

import SettingsPage from "@/app/settings/page";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.roster = "data";
  window.localStorage.clear();
  window.history.replaceState({}, "", "/settings");
  mocks.api.mockImplementation((path: string, opts?: { method?: string }) => {
    if (opts?.method) return Promise.resolve({});
    if (path === "/api/whoami")
      return Promise.resolve({
        user: "operator",
        strong: true,
        admin: true,
        can_administer: true,
        keys_minted: 1,
      });
    if (path === "/api/users?all=1") {
      if (mocks.roster === "failed") return Promise.reject(new Error("roster exploded"));
      if (mocks.roster === "pending") return new Promise(() => {});
      return Promise.resolve(mocks.roster === "empty" ? [] : roster);
    }
    if (path === "/api/users/growth-interests")
      return Promise.resolve({ interests: "" });
    if (path === "/api/agents/status")
      return Promise.resolve({ review_gate: true });
    if (path === "/api/settings/model")
      return Promise.resolve({
        model: "",
        override: null,
        ignored: "",
        default: "",
        menu: [],
        menu_error: "",
        applies: false,
        provider: "mock",
      });
    if (path === "/api/settings/context-strategy")
      return Promise.resolve({
        strategy: "sliding",
        override: "",
        default: "sliding",
        choices: ["sliding"],
        applies: true,
      });
    if (path === "/api/settings/tuning") return Promise.resolve([]);
    return Promise.resolve([]);
  });
});

const rowFor = async (name: string) => {
  const team = screen.queryByRole("link", { name: "Team" });
  if (team) fireEvent.click(team);
  await waitFor(() =>
    expect(document.getElementById("settings-team")?.hidden).toBe(false),
  );
  const nameNode = await screen.findByText(name, { selector: "li > span" });
  return within(nameNode.closest("li")!);
};

const rosterWrites = () =>
  mocks.api.mock.calls.filter(
    ([path, opts]) =>
      opts?.method === "POST" &&
      (String(path).includes("/rename") || String(path).includes("/active")),
  );

describe("Settings roster confirmations", () => {
  it("shows one top-level Settings section at a time", () => {
    render(<SettingsPage />);
    const you = document.getElementById("settings-you")!;
    const team = document.getElementById("settings-team")!;
    expect(you.hidden).toBe(false);
    expect(team.hidden).toBe(true);

    fireEvent.click(screen.getByRole("link", { name: "Team" }));

    expect(you.hidden).toBe(true);
    expect(team.hidden).toBe(false);
  });

  it("opens a valid direct hash", () => {
    window.history.replaceState({}, "", "/settings#settings-team");
    render(<SettingsPage />);
    expect(document.getElementById("settings-team")?.hidden).toBe(false);
    expect(document.getElementById("settings-you")?.hidden).toBe(true);
  });

  it("shows roster loading without claiming that nobody exists", async () => {
    mocks.roster = "pending";
    render(<SettingsPage />);
    fireEvent.click(screen.getByRole("link", { name: "Team" }));

    expect(await screen.findByText("Loading the team roster…")).toBeTruthy();
    expect(screen.queryByText("Nobody yet.")).toBeNull();
  });

  it("shows a roster error without claiming an empty team", async () => {
    mocks.roster = "failed";
    render(<SettingsPage />);
    fireEvent.click(screen.getByRole("link", { name: "Team" }));

    expect(
      await screen.findByText(/Could not load this page: roster exploded/),
    ).toBeTruthy();
    expect(screen.queryByText("Nobody yet.")).toBeNull();
  });

  it("shows a true empty roster only after a successful read", async () => {
    mocks.roster = "empty";
    render(<SettingsPage />);
    fireEvent.click(screen.getByRole("link", { name: "Team" }));

    expect(await screen.findByText("No teammates are on the roster.")).toBeTruthy();
    expect(screen.queryByText("Nobody yet.")).toBeNull();
  });

  it("describes a rename without calling it irreversible", async () => {
    render(<SettingsPage />);
    const row = await rowFor("Ava");
    fireEvent.click(row.getByRole("button", { name: "rename…" }));
    fireEvent.change(row.getByLabelText("Rename Ava to"), {
      target: { value: "Cleo" },
    });
    fireEvent.click(row.getByRole("button", { name: "save" }));

    const consequence = row.getByText(
      /Team-visible attribution and crew memberships will move to Cleo.*Existing activity history will stay under Ava/i,
    );
    const confirm = row.getByRole("button", { name: "Rename Ava" });
    expect(confirm.getAttribute("aria-describedby")).toBe(consequence.id);
    expect(row.queryByText(/cannot later be separated/i)).toBeNull();
    expect(rosterWrites()).toHaveLength(0);

    fireEvent.click(confirm);
    await waitFor(() =>
      expect(mocks.api).toHaveBeenCalledWith("/api/users/Ava/rename", {
        method: "POST",
        body: JSON.stringify({ new_name: "Cleo", merge: false }),
      }),
    );
  });

  it("warns that a merge cannot later be separated", async () => {
    render(<SettingsPage />);
    const row = await rowFor("Ava");
    fireEvent.click(row.getByRole("button", { name: "rename…" }));
    fireEvent.change(row.getByLabelText("Rename Ava to"), {
      target: { value: "Bo" },
    });
    fireEvent.click(row.getByRole("button", { name: "save" }));

    const consequence = row.getByText(
      /Team-visible attribution and crew memberships will be combined under Bo.*Active personal API keys for Ava will stay active under Bo.*Existing activity history under each name will stay as written.*This merge cannot later be separated/i,
    );
    const confirm = row.getByRole("button", { name: "Merge Ava into Bo" });
    expect(confirm.getAttribute("aria-describedby")).toBe(consequence.id);
    expect(rosterWrites()).toHaveLength(0);

    fireEvent.click(confirm);
    await waitFor(() =>
      expect(mocks.api).toHaveBeenCalledWith("/api/users/Ava/rename", {
        method: "POST",
        body: JSON.stringify({ new_name: "Bo", merge: true }),
      }),
    );
  });

  it("states that deactivation blocks access and revokes personal keys", async () => {
    render(<SettingsPage />);
    const row = await rowFor("Ava");
    fireEvent.click(row.getByRole("button", { name: "deactivate…" }));

    const consequence = row.getByText(
      /Ava will lose access.*History will stay.*All personal API keys for Ava will be revoked/i,
    );
    const confirm = row.getByRole("button", { name: "Deactivate Ava" });
    expect(confirm.getAttribute("aria-describedby")).toBe(consequence.id);
    expect(rosterWrites()).toHaveLength(0);

    fireEvent.click(confirm);
    await waitFor(() =>
      expect(mocks.api).toHaveBeenCalledWith("/api/users/Ava/active", {
        method: "POST",
        body: JSON.stringify({ active: false }),
      }),
    );
  });

  it("states that reactivation does not restore revoked keys", async () => {
    render(<SettingsPage />);
    const row = await rowFor("Dana");
    fireEvent.click(row.getByRole("button", { name: "reactivate…" }));

    const consequence = row.getByText(
      /Dana will regain access.*Revoked personal API keys will stay revoked.*Create a new key if Dana needs one/i,
    );
    const confirm = row.getByRole("button", { name: "Reactivate Dana" });
    expect(confirm.getAttribute("aria-describedby")).toBe(consequence.id);
    expect(rosterWrites()).toHaveLength(0);

    fireEvent.click(confirm);
    await waitFor(() =>
      expect(mocks.api).toHaveBeenCalledWith("/api/users/Dana/active", {
        method: "POST",
        body: JSON.stringify({ active: true }),
      }),
    );
  });
});

describe("Settings calendar disclosure", () => {
  it("names the shared data and the tokenized URL boundary", async () => {
    render(<SettingsPage />);
    expect(
      await screen.findByText(
        /team event titles and descriptions.*milestone and promise dates.*calendar app can copy this data outside the network/i,
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(
        /tokenized URL grants feed access only to a client that can reach the Skein API.*Treat the full URL as a credential/i,
      ),
    ).toBeTruthy();
  });
});
