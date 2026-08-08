import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** The model section states prices and token counts, so every rule that
 *  governs a string carrying a number applies (CLAUDE.md): no warmth, and the
 *  sentence must agree with what the server reports.
 *
 *  The claim a reader acts on and cannot check is "this model is in force".
 *  A section that hid a stale stored pick, or rendered a menu the server
 *  voided, would tell an administrator the deployment runs one model while it
 *  runs another.
 */

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
};

const mode: { pick: unknown } = { pick: PICK };
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => {
      if (path === "/api/settings/model") return Promise.resolve(mode.pick);
      if (path === "/api/whoami")
        return Promise.resolve({
          user: "boss",
          strong: true,
          admin: true,
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
      text.indexOf("The model every chat runs on"),
      text.indexOf("Long chats"),
    );
    expect(ours).not.toMatch(/!|’|'(s|re|ll|t)\b/);
  });
});
