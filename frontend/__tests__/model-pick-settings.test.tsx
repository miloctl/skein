import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** The model section states prices and token counts, so every rule that
 *  governs a string carrying a number applies (CLAUDE.md): no warmth, and the
 *  sentence must agree with what the server reports.
 *
 *  The claim a reader acts on and cannot check is "this team default is in force".
 *  A section that hid a stale stored pick, or rendered a menu the server
 *  voided, would tell an administrator the deployment runs one model while it
 *  runs another.
 */

const SUMMARY = {
  scope: "team_default",
  note: "This is the team default. Persona overrides can use a different model or parameters.",
  rows: [
    { id: "provider", label: "Provider", value: "anthropic", source: "SKEIN_MODEL_PROVIDER" },
    { id: "model", label: "Team-default model", value: "opus", source: "Settings → Model (team)" },
    { id: "output_cap", label: "Output cap", value: "8,192 tokens", source: "selected model entry" },
    {
      id: "attachments",
      label: "Attachments",
      value: "Direct: document. Images: vision sidecar.",
      source: "selected model entry + SKEIN_VISION_MODEL",
    },
    { id: "vision_sidecar", label: "Vision sidecar", value: "qwen3.5:cloud", source: "SKEIN_VISION_MODEL" },
    { id: "long_chat", label: "Long chats", value: "sliding", source: "SKEIN_CONTEXT_STRATEGY" },
    { id: "model_menu", label: "Model menu", value: "2 models", source: "SKEIN_MODELS_FILE" },
    { id: "prices", label: "Prices", value: "Set for team-default model", source: "selected model entry" },
    { id: "parameters", label: "Parameters", value: "3 parameters", source: "SKEIN_MODEL_PARAMS_FILE + selected model entry" },
  ],
};

const PICK = {
  model: "opus",
  override: {
    provider: "anthropic",
    model_id: "opus",
    set_by: "boss",
    updated_at: "2026-08-08T10:00:00+00:00",
  },
  ignored: "",
  default: "env-default",
  menu: [
    {
      id: "opus",
      label: "Opus — deep work",
      detail: "Slow and expensive.",
      max_tokens: 8192,
      context_tokens: 200000,
      price: [15, 75],
    },
    {
      id: "mini",
      label: "mini",
      detail: "",
      max_tokens: null,
      context_tokens: null,
      price: null,
    },
  ],
  menu_error: "",
  applies: true,
  provider: "anthropic",
  summary: SUMMARY,
};

const mode: { pick: unknown; post: "ok" | "refuse" } = {
  pick: PICK,
  post: "ok",
};
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, opts?: { method?: string }) => {
      if (path === "/api/settings/model" && opts?.method === "POST")
        return mode.post === "ok"
          ? Promise.resolve(mode.pick)
          : Promise.reject(
              new Error("HTTP 400: unknown model — expected one of: mini, opus"),
            );
      if (path === "/api/settings/model") return Promise.resolve(mode.pick);
      if (path === "/api/whoami")
        return Promise.resolve({
          user: "boss",
          strong: true,
          admin: true,
          can_administer: true,
          keys_minted: 1,
        });
      // every other panel stays mid-load, so nothing else renders a claim
      // that could be mistaken for one of ours
      return new Promise(() => {});
    },
    getUser: () => "tester",
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/settings" }));

import SettingsPage from "@/app/settings/page";

describe("the model section", () => {
  it("shows the server summary before the model controls", async () => {
    mode.pick = PICK;
    render(<SettingsPage />);

    const heading = await screen.findByRole("heading", { name: "In force" });
    expect(screen.getByText("qwen3.5:cloud")).toBeTruthy();
    expect(screen.getByText("8,192 tokens")).toBeTruthy();
    expect(screen.getByText("2 models")).toBeTruthy();
    expect(screen.getByText("3 parameters")).toBeTruthy();
    expect(screen.getByText("SKEIN_VISION_MODEL")).toBeTruthy();
    expect(
      screen.getByText(
        "This is the team default. Persona overrides can use a different model or parameters.",
      ),
    ).toBeTruthy();
    const radio = await screen.findByRole("radio", { name: /Opus — deep work/ });
    expect(
      heading.compareDocumentPosition(radio) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("does not invent a cap that hidden parameters can replace", async () => {
    mode.pick = {
      ...PICK,
      summary: {
        ...SUMMARY,
        rows: SUMMARY.rows.map((row) =>
          row.id === "output_cap"
            ? {
                ...row,
                value: "Set in parameters (value hidden)",
                source: "SKEIN_MODEL_PARAMS_FILE",
              }
            : row,
        ),
      },
    };
    render(<SettingsPage />);
    expect(await screen.findByText("Set in parameters (value hidden)")).toBeTruthy();
  });

  it("renders the menu with each entry's price and context size", async () => {
    mode.pick = PICK;
    render(<SettingsPage />);
    await screen.findByText(/Opus — deep work/);
    expect(
      screen.getByText(/\$15 in \/ \$75 out per million tokens/),
    ).toBeTruthy();
    expect(screen.getByText(/200,000-token context/)).toBeTruthy();
    // an entry with no tuning renders no empty qualifier line
    expect(screen.getByText("mini")).toBeTruthy();
  });

  it("names the provenance of an override in force", async () => {
    mode.pick = PICK;
    render(<SettingsPage />);
    const line = await screen.findByText(/Set by boss on 2026-08-08\./);
    expect(line.textContent).toMatch(/Overrides the deployment default/);
    expect(line.textContent).toMatch(/env-default/);
  });

  it("reports a stale stored pick instead of hiding it", async () => {
    mode.pick = {
      ...PICK,
      model: "env-default",
      ignored: "The picked model is no longer in the menu.",
      override: { ...PICK.override, model_id: "retired-model" },
    };
    render(<SettingsPage />);
    // the stored id AND the reason, both named — the reader cannot otherwise
    // tell that the model they picked is not the one running
    const warning = await screen.findByText(/is not in use/);
    expect(warning.textContent).toMatch(/retired-model/);
    expect(warning.textContent).toMatch(/no longer in the menu/);
  });

  it("keeps a stored pick visible and clearable when the menu vanishes", async () => {
    // an admin who removes or breaks SKEIN_MODELS still has a stored pick —
    // it must stay on screen with its clear button, or the deployment holds
    // state its own settings page cannot see or drop
    mode.pick = {
      ...PICK,
      model: "env-default",
      ignored: "The picked model is no longer in the menu.",
      menu: [],
      applies: false,
    };
    render(<SettingsPage />);
    const warning = await screen.findByText(/is not in use/);
    expect(warning.textContent).toMatch(/opus/);
    expect(
      screen.getByRole("button", { name: /Use the deployment default/ }),
    ).toBeTruthy();
  });

  it("names the model in force when it is outside the menu", async () => {
    // no radio is checked in this state — without the line, the section
    // shows choices under "the model every chat runs on" and never says
    // which model that is
    mode.pick = { ...PICK, model: "env-default", override: null };
    render(<SettingsPage />);
    const line = await screen.findByText(/is in force/);
    expect(line.textContent).toMatch(/env-default/);
    expect(line.textContent).toMatch(/It is not in the menu\./);
    const radios = screen.getAllByRole("radio");
    expect(radios.some((r) => (r as HTMLInputElement).checked)).toBe(false);
  });

  it("routes a served refusal to Not saved, never to unreachable", async () => {
    mode.pick = PICK;
    mode.post = "refuse";
    render(<SettingsPage />);
    await screen.findByText(/Opus — deep work/);
    fireEvent.click(screen.getByRole("radio", { name: /mini/ }));
    const status = await screen.findByText(/Not saved\./);
    // a server that answered 400 is not an unreachable backend — saying so
    // sends the reader to check a server that is running
    expect(status.textContent).not.toMatch(/unreachable/i);
    mode.post = "ok";
  });

  it("confirms a save in the settled wording", async () => {
    mode.pick = PICK;
    mode.post = "ok";
    render(<SettingsPage />);
    await screen.findByText(/Opus — deep work/);
    fireEvent.click(screen.getByRole("radio", { name: /mini/ }));
    await screen.findByText(/Saved\. Every chat uses it from its next message\./);
  });

  it("shows the server's registry fault instead of a menu", async () => {
    mode.pick = {
      ...PICK,
      applies: false,
      menu: [],
      menu_error: "SKEIN_MODELS is unusable: entry 1 has no usable id. The model menu is off.",
    };
    render(<SettingsPage />);
    await screen.findByText(/SKEIN_MODELS is unusable/);
    expect(screen.queryByText(/Opus — deep work/)).toBeNull();
  });

  it("says the setting is not in use on mock, and shows no picker", async () => {
    mode.pick = {
      ...PICK,
      model: "",
      override: null,
      applies: false,
      provider: "mock",
    };
    render(<SettingsPage />);
    await waitFor(() =>
      expect(
        screen.getAllByText(/No model is connected\. This setting is not in use\./)
          .length,
      ).toBeGreaterThan(0),
    );
    expect(screen.queryByText(/Opus — deep work/)).toBeNull();
  });

  it("carries no warmth, because its lines hold numbers", async () => {
    mode.pick = PICK;
    const { container } = render(<SettingsPage />);
    await screen.findByText(/Opus — deep work/);
    const text = container.textContent ?? "";
    const ours = text.slice(
      text.indexOf("This section shows the team-default model configuration"),
      text.indexOf("Long chats"),
    );
    expect(ours).not.toMatch(/!|’|'(s|re|ll|t|m|ve|d)\b/);
  });
});
