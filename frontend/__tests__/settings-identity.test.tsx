import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  identity: {
    user: "operator",
    strong: true,
    admin: false,
    can_administer: true,
    keys_minted: 1,
  },
  calls: [] as string[],
  requests: [] as { path: string; init?: RequestInit }[],
  identityError: "",
  modelPromise: null as Promise<unknown> | null,
  createdKey: "sk-skein-created-once",
  tunables: [] as unknown[],
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "operator",
    api: (path: string, init?: RequestInit) => {
      state.calls.push(path);
      state.requests.push({ path, init });
      if (path === "/api/whoami")
        return state.identityError
          ? Promise.reject(new Error(state.identityError))
          : Promise.resolve(state.identity);
      if (path === "/api/users/growth-interests")
        return Promise.resolve({ interests: "" });
      if (path === "/api/agents/status")
        return Promise.resolve({ review_gate: true });
      if (path === "/api/settings/model")
        return (
          state.modelPromise ??
          Promise.resolve({
            model: "mini",
            override: null,
            ignored: "",
            default: "mini",
            menu: [
              {
                id: "mini",
                label: "Mini",
                detail: "",
                max_tokens: null,
                context_tokens: null,
                price: null,
              },
            ],
            menu_error: "",
            applies: true,
            provider: "ollama",
            summary: {
              scope: "team_default",
              note: "This is the team default. Persona overrides can use a different model or parameters.",
              rows: [
                {
                  id: "provider",
                  label: "Provider",
                  value: "ollama",
                  source: "SKEIN_MODEL_PROVIDER",
                },
                {
                  id: "model",
                  label: "Team-default model",
                  value: "mini",
                  source: "SKEIN_MODEL_ID",
                },
              ],
            },
          })
        );
      if (path === "/api/settings/context-strategy")
        return Promise.resolve({
          strategy: "sliding",
          override: "",
          default: "sliding",
          choices: ["sliding"],
          applies: true,
        });
      if (path === "/api/settings/tuning")
        return Promise.resolve(state.tunables);
      if (path === "/api/keys")
        return Promise.resolve({
          id: 1,
          key: state.createdKey,
          prefix: "created",
        });
      return Promise.resolve([]);
    },
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/settings" }));

import SettingsPage from "@/app/settings/page";

beforeEach(() => {
  state.identity = {
    user: "operator",
    strong: true,
    admin: false,
    can_administer: true,
    keys_minted: 1,
  };
  state.calls.length = 0;
  state.requests.length = 0;
  state.identityError = "";
  state.modelPromise = null;
  state.tunables = [];
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

describe("Settings identity states", () => {
  it("uses can_administer for AdminUser controls", async () => {
    render(<SettingsPage />);

    expect(
      await screen.findByRole("button", { name: "Back up now" }),
    ).toBeTruthy();
    expect(
      ((await screen.findByRole("radio", { name: /Mini/ })) as HTMLInputElement)
        .disabled,
    ).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: /Customize & share/ }));
    expect(
      (
        screen.getByRole("button", {
          name: "Make this the team default",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false);
  });

  it("groups cards under four fragment-linked settings sections", async () => {
    render(<SettingsPage />);
    await screen.findByRole("heading", { name: "In force" });

    const nav = screen.getByRole("navigation", { name: "Settings sections" });
    expect(nav.className).toContain("flex-wrap");
    expect(nav.className).toContain("lg:sticky");
    for (const [name, id] of [
      ["You", "settings-you"],
      ["Connections", "settings-connections"],
      ["AI runtime", "settings-ai-runtime"],
      ["Team", "settings-team"],
    ]) {
      expect(screen.getByRole("link", { name }).getAttribute("href")).toBe(
        `#${id}`,
      );
      expect(screen.getByRole("heading", { level: 2, name })).toBeTruthy();
    }
    expect(screen.getByRole("link", { name: "You" }).getAttribute("aria-current")).toBe(
      "location",
    );
    expect(nav.querySelectorAll('[aria-current="location"]')).toHaveLength(1);

    const you = document.getElementById("settings-you");
    const connections = document.getElementById("settings-connections");
    const runtime = document.getElementById("settings-ai-runtime");
    const team = document.getElementById("settings-team");
    expect(you && connections && runtime && team).toBeTruthy();
    expect(
      within(you!).getByRole("heading", { level: 3, name: "Appearance" }),
    ).toBeTruthy();
    expect(
      within(you!).getByRole("heading", { level: 3, name: "Attached files" }),
    ).toBeTruthy();
    expect(
      within(connections!).getByRole("heading", {
        level: 3,
        name: "Connect your own AI agent (optional)",
      }),
    ).toBeTruthy();
    expect(
      within(runtime!).getByRole("heading", { level: 3, name: "Model (team)" }),
    ).toBeTruthy();
    expect(
      within(runtime!).getByRole("heading", { level: 4, name: "In force" }),
    ).toBeTruthy();
    expect(
      within(team!).getByRole("heading", { level: 3, name: "Backups (team)" }),
    ).toBeTruthy();

    expect(
      screen
        .getByRole("link", { name: "Open the field guide" })
        .getAttribute("href"),
    ).toBe("/guide");
    expect(screen.queryByRole("heading", { name: "Field guide" })).toBeNull();
  });

  it("shows model state but not administrator controls to a strong non-administrator", async () => {
    state.identity = { ...state.identity, can_administer: false };
    render(<SettingsPage />);

    expect(
      (
        await screen.findAllByText(
          "You do not have administrator access. Ask an administrator for access.",
        )
      ).length,
    ).toBeGreaterThan(0);
    expect(
      await screen.findByRole("heading", { name: "In force" }),
    ).toBeTruthy();
    await waitFor(() => expect(state.calls).toContain("/api/settings/model"));
    expect(state.calls).not.toContain("/api/settings/context-strategy");
    expect(state.calls).not.toContain("/api/settings/tuning");
    expect(screen.queryByRole("radio", { name: /Mini/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Back up now" })).toBeNull();
  });

  it("clears administrator data when credentials change", async () => {
    render(<SettingsPage />);
    expect(await screen.findByRole("radio", { name: /Mini/ })).toBeTruthy();

    state.identity = {
      ...state.identity,
      strong: false,
      can_administer: false,
    };
    window.dispatchEvent(new Event("storage"));

    expect(
      (
        await screen.findAllByText(
          /This action requires strong identity and administrator access/,
        )
      ).length,
    ).toBeGreaterThan(0);
    await waitFor(() =>
      expect(screen.queryByRole("radio", { name: /Mini/ })).toBeNull(),
    );
    expect(screen.queryByRole("button", { name: "Back up now" })).toBeNull();
  });

  it("clears the deployment-limit receipt when credentials change", async () => {
    state.tunables = [
      {
        name: "chat_cap",
        label: "Chat cap",
        value: 600,
        default: 600,
        override: null,
        floor: 60,
        ceiling: 3600,
        unit: "seconds",
        live: true,
        detail: "Longest allowed chat turn.",
        ignored: false,
      },
    ];
    render(<SettingsPage />);
    const input = await screen.findByLabelText("Chat cap");
    fireEvent.change(input, { target: { value: "900" } });
    const knob = input.closest("div.rounded-xl") as HTMLElement;
    fireEvent.click(within(knob).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Chat cap: 900 seconds.")).toBeTruthy();

    state.identity = {
      ...state.identity,
      strong: false,
      can_administer: false,
    };
    window.dispatchEvent(new Event("storage"));

    // the receipt is a value the section says only an administrator can
    // read — it must not survive into the next identity's page
    await waitFor(() =>
      expect(screen.queryByText("Chat cap: 900 seconds.")).toBeNull(),
    );
  });

  it("invalidates a model response and starts no replacement for anonymous", async () => {
    let finishModel!: (value: unknown) => void;
    state.modelPromise = new Promise((resolve) => {
      finishModel = resolve;
    });
    render(<SettingsPage />);
    await waitFor(() => expect(state.calls).toContain("/api/settings/model"));

    state.identity = {
      ...state.identity,
      user: "anonymous",
      strong: false,
      can_administer: false,
    };
    window.dispatchEvent(new Event("storage"));
    await waitFor(() =>
      expect(state.calls.filter((path) => path === "/api/whoami")).toHaveLength(
        2,
      ),
    );
    finishModel({
      model: "mini",
      override: null,
      ignored: "",
      default: "mini",
      menu: [],
      menu_error: "",
      applies: true,
      provider: "ollama",
      summary: {
        scope: "team_default",
        note: "old identity response",
        rows: [
          {
            id: "model",
            label: "Team-default model",
            value: "mini",
            source: "SKEIN_MODEL_ID",
          },
        ],
      },
    });

    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "In force" })).toBeNull(),
    );
    expect(
      state.calls.filter((path) => path === "/api/settings/model"),
    ).toHaveLength(1);
  });

  it("shows an identity failure instead of checking forever", async () => {
    state.identityError = "identity service failed";
    render(<SettingsPage />);

    expect(
      (
        await screen.findAllByText(
          "Could not load this page: identity service failed",
        )
      ).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText("Checking identity…")).toBeNull();
  });

  it("confirms the browser-only scope before deleting a stored key", async () => {
    window.localStorage.setItem("skein-key", "sk-skein-stored");
    render(<SettingsPage />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Delete from browser…" }),
    );
    const consequence = screen.getByText(
      "Delete this key from this browser? This does not revoke a server key.",
    );
    const confirm = screen.getByRole("button", { name: "Delete from browser" });
    expect(confirm.getAttribute("aria-describedby")).toBe(consequence.id);
    expect(window.localStorage.getItem("skein-key")).toBe("sk-skein-stored");

    fireEvent.click(confirm);
    await waitFor(() =>
      expect(window.localStorage.getItem("skein-key")).toBeNull(),
    );
    expect(
      await screen.findByText(
        "The browser no longer stores this key. No server key was revoked.",
      ),
    ).toBeTruthy();
  });

  it("does not delete a key that replaced the confirmed browser value", async () => {
    window.localStorage.setItem("skein-key", "sk-skein-first");
    render(<SettingsPage />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Delete from browser…" }),
    );

    window.localStorage.setItem("skein-key", "sk-skein-second");
    window.dispatchEvent(new Event("storage"));
    fireEvent.click(
      screen.getByRole("button", { name: "Delete from browser" }),
    );

    expect(window.localStorage.getItem("skein-key")).toBe("sk-skein-second");
    expect(
      await screen.findByText(
        "The stored key changed after this confirmation. Confirm the deletion again.",
      ),
    ).toBeTruthy();
  });

  it("uses draft-specific guidance when a replacement key is refused", async () => {
    window.localStorage.setItem("skein-key", "sk-skein-valid-stored");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: () =>
          Promise.resolve({
            detail:
              "This personal API key is invalid or revoked. Delete it from this browser, then save a valid key.",
          }),
      }),
    );
    render(<SettingsPage />);

    fireEvent.change(await screen.findByLabelText("Personal API key"), {
      target: { value: "sk-skein-invalid-draft" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Test & save" }));

    expect(
      await screen.findByText(
        "This key did not establish strong identity. Check the key, then try again.",
      ),
    ).toBeTruthy();
    expect(screen.queryByText(/Delete it from this browser/)).toBeNull();
    expect(window.localStorage.getItem("skein-key")).toBe(
      "sk-skein-valid-stored",
    );
  });

  it("keeps deployment sign-in as the current identity after saving a key", async () => {
    window.localStorage.setItem(
      "skein-oidc",
      JSON.stringify({
        access_token: "deployment-token",
        refresh_token: "refresh",
        expires_at: Date.now() + 3_600_000,
        user: "operator",
      }),
    );
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            user: "other-owner",
            strong: true,
            admin: false,
            can_administer: false,
            keys_minted: 1,
          }),
      }),
    );
    render(<SettingsPage />);

    fireEvent.change(await screen.findByLabelText("Personal API key"), {
      target: { value: "sk-skein-other-owner" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Test & save" }));

    expect(
      await screen.findByText(
        "Key works and is stored for other-owner. Deployment sign-in remains active as operator. The stored key takes effect after you sign out.",
      ),
    ).toBeTruthy();
    expect(
      screen.queryByText(/key owner controls private surfaces/i),
    ).toBeNull();
  });

  it("lets a keyless deployment sign-in create its first personal key", async () => {
    state.identity = { ...state.identity, keys_minted: 0 };
    render(<SettingsPage />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Create a personal API key" }),
    );

    expect(
      state.requests.some(
        ({ path, init }) =>
          path === "/api/keys" &&
          init?.method === "POST" &&
          init.body === JSON.stringify({ label: "CLI and git hooks" }),
      ),
    ).toBe(true);
    expect(await screen.findByText(state.createdKey)).toBeTruthy();
    expect(
      screen.getByText("Copy this key now. Skein will not show it again."),
    ).toBeTruthy();
  });
});
