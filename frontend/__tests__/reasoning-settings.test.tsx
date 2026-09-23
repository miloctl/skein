import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** The team reasoning level. The claim a reader acts on is "this level is in
 *  force for the team model". A section that offered a level the model does
 *  not declare, or hid a saved level the model stopped offering, would
 *  describe reasoning the deployment does not do. */

type Reasoning = {
  level: string;
  override: string;
  levels: string[];
  ignored: string;
  applies: boolean;
};

const requests: { path: string; method: string; body?: string }[] = [];
const mode: {
  reasoning: Reasoning;
  post: "ok" | "refuse";
  who: { user: string; strong: boolean; admin: boolean; can_administer: boolean; keys_minted: number };
} = {
  reasoning: { level: "", override: "", levels: ["low", "high"], ignored: "", applies: true },
  post: "ok",
  who: { user: "boss", strong: true, admin: true, can_administer: true, keys_minted: 1 },
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, opts?: RequestInit) => {
      requests.push({ path, method: opts?.method ?? "GET", body: opts?.body as string | undefined });
      if (path === "/api/settings/reasoning" && opts?.method === "POST") {
        if (mode.post === "refuse")
          return Promise.reject(
            new Error("HTTP 400: The team model has no level with that name. Use one of: low, high."),
          );
        const level = JSON.parse(String(opts.body)).level;
        mode.reasoning = { ...mode.reasoning, level, override: level };
        return Promise.resolve(mode.reasoning);
      }
      if (path === "/api/settings/reasoning") return Promise.resolve(mode.reasoning);
      if (path === "/api/whoami") return Promise.resolve(mode.who);
      // every other panel stays mid-load, so nothing else renders a claim
      // that could be mistaken for one of ours
      return new Promise(() => {});
    },
    getUser: () => "tester",
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/settings" }));

import SettingsPage from "@/app/settings/page";

function renderSettings() {
  const result = render(<SettingsPage />);
  fireEvent.click(screen.getByRole("link", { name: "AI runtime" }));
  return result;
}

function section() {
  return screen.getByRole("heading", { name: "Reasoning (team)" }).closest("section")!;
}

beforeEach(() => {
  mode.reasoning = { level: "", override: "", levels: ["low", "high"], ignored: "", applies: true };
  mode.post = "ok";
  mode.who = { user: "boss", strong: true, admin: true, can_administer: true, keys_minted: 1 };
  requests.length = 0;
});

describe("the reasoning section", () => {
  it("offers the levels of the team model and saves one", async () => {
    renderSettings();
    const high = await screen.findByRole("radio", { name: "high" }, { timeout: 5_000 });
    expect((screen.getByRole("radio", { name: "Model default" }) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByRole("radio", { name: "low" })).toBeTruthy();
    expect(screen.queryByRole("radio", { name: "xhigh" })).toBeNull();
    const summaryReads = requests.filter((r) => r.path === "/api/settings/model").length;

    fireEvent.click(high);
    await screen.findByText("Saved. It applies from the next message in each chat.");
    expect(requests).toContainEqual({
      path: "/api/settings/reasoning",
      method: "POST",
      body: JSON.stringify({ level: "high" }),
    });
    expect((high as HTMLInputElement).checked).toBe(true);
    // the In force summary names the team level, so it is read again
    await waitFor(() =>
      expect(requests.filter((r) => r.path === "/api/settings/model").length).toBeGreaterThan(summaryReads),
    );
  });

  it("shows a refusal and keeps the level that is in force", async () => {
    mode.post = "refuse";
    renderSettings();
    fireEvent.click(await screen.findByRole("radio", { name: "high" }, { timeout: 5_000 }));
    await screen.findByText(/^Not saved\. .*Use one of: low, high\.$/);
    expect((screen.getByRole("radio", { name: "Model default" }) as HTMLInputElement).checked).toBe(true);
  });

  it("gives a reader without administrator access no working control", async () => {
    mode.who = { user: "ava", strong: false, admin: false, can_administer: false, keys_minted: 0 };
    renderSettings();
    const high = await screen.findByRole("radio", { name: "high" }, { timeout: 5_000 });
    expect((high as HTMLInputElement).disabled).toBe(true);
    expect(requests.some((r) => r.method === "POST")).toBe(false);
  });

  it("says where levels come from when the team model has none", async () => {
    mode.reasoning = { ...mode.reasoning, levels: [] };
    renderSettings();
    await screen.findByText(/The team model has no reasoning levels\./, {}, { timeout: 5_000 });
    expect(screen.queryByRole("radio", { name: "Model default" })).toBeNull();
  });

  it("says the setting is not in use when no model is connected", async () => {
    mode.reasoning = { ...mode.reasoning, levels: [], applies: false };
    renderSettings();
    await screen.findByText(/No model is connected\. This setting is not in use\./, {}, { timeout: 5_000 });
    expect(screen.queryByText(/has no reasoning levels/)).toBeNull();
  });

  it("reports a saved level the team model stopped offering", async () => {
    mode.reasoning = {
      level: "",
      override: "high",
      levels: ["low"],
      ignored: "The team model does not offer this level.",
      applies: true,
    };
    renderSettings();
    await screen.findByText(
      "The saved level is high. The team model does not offer this level. Chats use the default of the model.",
      {},
      { timeout: 5_000 },
    );
    expect((screen.getByRole("radio", { name: "Model default" }) as HTMLInputElement).checked).toBe(true);
  });

  it("writes its copy without contractions or exclamation marks", async () => {
    renderSettings();
    await screen.findByRole("radio", { name: "high" }, { timeout: 5_000 });
    const text = section().textContent ?? "";
    expect(text).not.toMatch(/\b\w+'(t|s|re|ll|ve|d)\b/);
    expect(text).not.toContain("!");
  });
});
