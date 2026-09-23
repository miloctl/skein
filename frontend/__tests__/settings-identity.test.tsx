import {
  act,
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
  automationEnabled: true,
  automationWritePromise: null as Promise<unknown> | null,
  automationReadFailsAfterWrite: false,
  authMode: "trusted-header",
  session: { authenticated: false, user: "anonymous", strong: false, auth_method: "", csrf_token: "", status: "ready", error: "" },
  keyExchange: vi.fn(),
  logout: vi.fn(),
  tunables: [] as unknown[],
  interestsFail: false,
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
      if (path === "/api/users/growth-interests" && !init?.method)
        return state.interestsFail
          ? Promise.reject(new Error("growth interests are unavailable"))
          : Promise.resolve({ interests: "" });
      if (path === "/api/agents/status")
        return Promise.resolve({ review_gate: true });
      if (path === "/api/settings/agent-automation") {
        if (init?.method === "POST") {
          if (state.automationWritePromise) return state.automationWritePromise;
          state.automationEnabled = Boolean(
            JSON.parse(String(init.body)).enabled,
          );
          return Promise.resolve({ enabled: state.automationEnabled });
        }
        if (
          state.automationReadFailsAfterWrite &&
          state.requests.some(
            (request) =>
              request.path === "/api/settings/agent-automation" &&
              request.init?.method === "POST",
          )
        )
          return Promise.reject(new Error("automation refresh failed"));
        return Promise.resolve({ enabled: state.automationEnabled });
      }
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
      if (path === "/api/keys") return Promise.resolve([]);
      return Promise.resolve([]);
    },
  };
});

vi.mock("@/lib/auth", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/auth")>();
  return {
    ...real,
    authConfig: () => Promise.resolve({ mode: state.authMode, error: "" }),
    sessionSnapshot: () => state.session,
    trustedHeaderIdentity: () => state.authMode === "trusted-header" && !state.session.authenticated,
    signInWithKey: state.keyExchange,
    signOut: state.logout,
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/settings" }));

import SettingsPage from "@/app/settings/page";
import { dismissStatus, getStatus } from "@/lib/status";

function openSettingsSection(name: "You" | "Connections" | "AI runtime" | "Team") {
  fireEvent.click(screen.getByRole("link", { name }));
}

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
  state.automationEnabled = true;
  state.automationWritePromise = null;
  state.automationReadFailsAfterWrite = false;
  state.tunables = [];
  state.interestsFail = false;
  state.authMode = "trusted-header";
  state.session = { authenticated: false, user: "anonymous", strong: false, auth_method: "", csrf_token: "", status: "ready", error: "" };
  state.keyExchange.mockReset();
  state.keyExchange.mockResolvedValue(undefined);
  state.logout.mockReset();
  state.logout.mockResolvedValue(undefined);
  window.localStorage.clear();
  window.history.replaceState({}, "", "/settings");
  vi.unstubAllGlobals();
});

describe("Settings identity states", () => {
  it("uses can_administer for AdminUser controls", async () => {
    render(<SettingsPage />);

    openSettingsSection("Team");
    expect(
      await screen.findByRole("button", { name: "Back up now" }),
    ).toBeTruthy();

    openSettingsSection("AI runtime");
    expect(
      ((await screen.findByRole("radio", { name: /Mini/ })) as HTMLInputElement)
        .disabled,
    ).toBe(false);

    openSettingsSection("You");
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
      expect(document.getElementById(id)).toBeTruthy();
    }
    expect(screen.getByRole("link", { name: "You" }).getAttribute("aria-current")).toBe(
      "location",
    );
    expect(nav.querySelectorAll('[aria-current="location"]')).toHaveLength(1);

    const you = document.getElementById("settings-you")!;
    expect(
      within(you).getByRole("heading", { level: 3, name: "Appearance" }),
    ).toBeTruthy();
    expect(
      within(you).getByRole("heading", { level: 3, name: "Attached files" }),
    ).toBeTruthy();

    openSettingsSection("Connections");
    expect(
      screen.getByRole("heading", {
        level: 3,
        name: "Connect your own AI agent (optional)",
      }),
    ).toBeTruthy();

    openSettingsSection("AI runtime");
    expect(
      screen.getByRole("heading", { level: 3, name: "Model (team)" }),
    ).toBeTruthy();
    expect(
      await screen.findByRole("heading", { level: 4, name: "In force" }),
    ).toBeTruthy();

    openSettingsSection("Team");
    expect(
      screen.getByRole("heading", { level: 3, name: "Backups (team)" }),
    ).toBeTruthy();
    expect(nav.querySelectorAll('[aria-current="location"]')).toHaveLength(1);

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
    openSettingsSection("AI runtime");

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

    openSettingsSection("Team");
    expect(screen.queryByRole("button", { name: "Back up now" })).toBeNull();
  });

  it("clears administrator data when credentials change", async () => {
    render(<SettingsPage />);
    openSettingsSection("AI runtime");
    expect(await screen.findByRole("radio", { name: /Mini/ })).toBeTruthy();

    state.identity = {
      ...state.identity,
      strong: false,
      can_administer: false,
    };
    window.dispatchEvent(new Event("skein-identity-change"));

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

    openSettingsSection("Team");
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
    openSettingsSection("AI runtime");
    const input = await screen.findByLabelText("Chat cap");
    fireEvent.change(input, { target: { value: "900" } });
    const knob = input.closest("div.rounded-xl") as HTMLElement;
    fireEvent.click(
      within(knob).getByRole("button", { name: "Save Chat cap" }),
    );
    expect(await screen.findByText("Chat cap: 900 seconds.")).toBeTruthy();

    state.identity = {
      ...state.identity,
      strong: false,
      can_administer: false,
    };
    window.dispatchEvent(new Event("skein-identity-change"));

    // the receipt is a value the section says only an administrator can
    // read — it must not survive into the next identity's page
    await waitFor(() =>
      expect(screen.queryByText("Chat cap: 900 seconds.")).toBeNull(),
    );
  });

  it("uses the automation write response without a fallible refresh", async () => {
    state.automationReadFailsAfterWrite = true;
    render(<SettingsPage />);
    openSettingsSection("AI runtime");

    fireEvent.click(
      await screen.findByRole("button", { name: "Pause unattended runs" }),
    );

    expect(
      await screen.findByRole("button", { name: "Resume unattended runs" }),
    ).toBeTruthy();
    expect(
      await screen.findByText(
        "Paused. A run in progress stops at its next step.",
      ),
    ).toBeTruthy();
    expect(
      state.calls.filter((path) => path === "/api/settings/agent-automation"),
    ).toHaveLength(2);
  });

  it("drops a pending automation receipt when OIDC identity changes", async () => {
    let finishWrite!: (value: unknown) => void;
    state.automationWritePromise = new Promise((resolve) => {
      finishWrite = resolve;
    });
    render(<SettingsPage />);
    openSettingsSection("AI runtime");

    fireEvent.click(
      await screen.findByRole("button", { name: "Pause unattended runs" }),
    );
    expect(await screen.findByText("Saving…")).toBeTruthy();

    state.identity = { ...state.identity, user: "next-operator" };
    state.automationEnabled = false;
    window.dispatchEvent(new Event("skein-identity-change"));

    expect(
      await screen.findByRole("button", { name: "Resume unattended runs" }),
    ).toBeTruthy();
    await act(async () => finishWrite({ enabled: false }));

    await waitFor(() =>
      expect(
        screen.queryByText("Paused. A run in progress stops at its next step."),
      ).toBeNull(),
    );
    expect(
      state.calls.filter((path) => path === "/api/settings/agent-automation"),
    ).toHaveLength(3);
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
    window.dispatchEvent(new Event("skein-identity-change"));
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

  it("keeps the identity through a theme paint and rereads it on an identity change", async () => {
    render(<SettingsPage />);
    await screen.findByText(/as operator/);
    const whoami = () => state.calls.filter((path) => path === "/api/whoami").length;
    const before = whoami();

    // lib/theme.ts dispatches this form on every paint, a hue drag included
    act(() => window.dispatchEvent(new Event("storage")));
    await act(async () => {});
    expect(whoami()).toBe(before);
    expect(screen.queryByText("Checking identity…")).toBeNull();

    act(() => window.dispatchEvent(new Event("skein-identity-change")));
    await waitFor(() => expect(whoami()).toBe(before + 1));
    act(() => window.dispatchEvent(new StorageEvent("storage", { key: "skein-user" })));
    await waitFor(() => expect(whoami()).toBe(before + 2));
  });

  it("locks the growth interests field when its stored value cannot be read", async () => {
    state.interestsFail = true;
    render(<SettingsPage />);
    expect(await screen.findByText("Could not load this page: growth interests are unavailable")).toBeTruthy();
    const field = screen.getByLabelText("Growth interests") as HTMLInputElement;
    fireEvent.change(field, { target: { value: "incident command" } });
    expect(
      (screen.getByRole("button", { name: "Save growth interests" }) as HTMLButtonElement).disabled,
    ).toBe(true);
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

  it("ends a browser session without revoking its automation key", async () => {
    state.session = { ...state.session, authenticated: true, user: "operator", strong: true, auth_method: "api-key", csrf_token: "csrf-operator" };
    render(<SettingsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Sign out of this browser" }));
    await waitFor(() => expect(state.logout).toHaveBeenCalledOnce());
    expect(state.requests.some(({ path, init }) => path.includes("/keys") && init?.method === "DELETE")).toBe(false);
    expect(screen.queryByRole("button", { name: /Delete from browser/ })).toBeNull();
  });

  it("reports a refused key exchange without changing the current session", async () => {
    state.session = { ...state.session, authenticated: true, user: "operator", strong: true, auth_method: "oidc", csrf_token: "csrf-operator" };
    state.keyExchange.mockRejectedValue(new Error("This key did not establish strong identity. Check the key, then try again."));
    render(<SettingsPage />);
    const input = await screen.findByLabelText("Personal API key") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "sk-skein-invalid-draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in with key" }));
    expect(await screen.findByText("This key did not establish strong identity. Check the key, then try again.")).toBeTruthy();
    expect(state.session.user).toBe("operator");
    expect(input.value).toBe("");
    expect(localStorage.getItem("skein-key")).toBeNull();
  });

  it("uses an entered key once instead of keeping a fallback identity", async () => {
    render(<SettingsPage />);
    const input = await screen.findByLabelText("Personal API key") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "sk-skein-other-owner" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in with key" }));
    await waitFor(() => expect(state.keyExchange).toHaveBeenCalledWith("sk-skein-other-owner"));
    expect(input.value).toBe("");
    await waitFor(() =>
      expect(getStatus()?.message).toBe("Signed in. This browser does not store your personal key."),
    );
    act(() => dismissStatus());
    expect(localStorage.getItem("skein-key")).toBeNull();
    expect(screen.queryByText(/takes effect after you sign out/)).toBeNull();
  });

  it("confirms a key sign-in that remounts the page, and moves focus to the content", async () => {
    // a new session re-keys SessionBoundary (components/auth-gate.tsx), which
    // remounts this page while the sign-in is still in flight
    const { rerender } = render(<SettingsPage key="anonymous" />);
    state.keyExchange.mockImplementation(async () => {
      rerender(<SettingsPage key="signed-in" />);
    });
    const input = await screen.findByLabelText("Personal API key");
    fireEvent.change(input, { target: { value: "sk-skein-owner" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in with key" }));

    await waitFor(() =>
      expect(getStatus()).toMatchObject({
        message: "Signed in. This browser does not store your personal key.",
        tone: "confirmation",
      }),
    );
    await waitFor(() => expect(document.activeElement?.id).toBe("content"));
    act(() => dismissStatus());
  });

  it("does not offer browser key minting to a strong identity without keys", async () => {
    state.identity = { ...state.identity, keys_minted: 0 };
    render(<SettingsPage />);
    await screen.findByText(/strong identity active as operator/);
    expect(screen.queryByRole("button", { name: "Create a personal API key" })).toBeNull();
    expect(state.requests.some(({ path, init }) => path === "/api/keys" && init?.method === "POST")).toBe(false);
    expect(screen.getByText(/Use the CLI or ask whoever runs the server to create/)).toBeTruthy();
  });

  it.each(["oidc", "api-key"])("does not offer a self-asserted name in %s mode", async (mode) => {
    state.authMode = mode;
    render(<SettingsPage />);
    await screen.findByLabelText("Personal API key");
    expect(screen.queryByLabelText("Your name")).toBeNull();
  });

});

it("keeps visible labels on personal settings fields", async () => {
  render(<SettingsPage />);
  for (const name of ["Your name", "Personal API key", "Growth interests"]) {
    const field = await screen.findByLabelText(name) as HTMLInputElement;
    expect(field.labels?.length).toBe(1);
    expect(field.labels?.[0].classList.contains("sr-only")).toBe(false);
  }
});
