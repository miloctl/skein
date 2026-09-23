import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

// Levels per team model, the shape services/settings.py::reasoning_level_state
// returns for each.
const LEVELS: Record<string, string[]> = { opus: ["low", "high"], mini: ["minimal"] };

const requests: { path: string; method: string; body?: string }[] = [];
const mode: {
  model: string;
  saved: string;
  levels: string[] | null;
  post: "ok" | "refuse";
  postPromise: Promise<unknown> | null;
  who: { user: string; strong: boolean; admin: boolean; can_administer: boolean; keys_minted: number };
} = {
  model: "opus",
  saved: "",
  levels: null,
  post: "ok",
  postPromise: null,
  who: { user: "boss", strong: true, admin: true, can_administer: true, keys_minted: 1 },
};

function state(): Reasoning {
  const levels = mode.levels ?? LEVELS[mode.model];
  const ignored = mode.saved && !levels.includes(mode.saved) ? "The team model does not offer this level." : "";
  return { level: ignored ? "" : mode.saved, override: mode.saved, levels, ignored, applies: true };
}

function pick() {
  return {
    model: mode.model,
    override: null,
    ignored: "",
    default: "opus",
    menu: [
      { id: "opus", label: "opus", detail: "", max_tokens: null, context_tokens: null, price: null },
      { id: "mini", label: "mini", detail: "", max_tokens: null, context_tokens: null, price: null },
    ],
    menu_error: "",
    applies: true,
    provider: "anthropic",
  };
}

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, opts?: RequestInit) => {
      const method = opts?.method ?? "GET";
      requests.push({ path, method, body: opts?.body as string | undefined });
      if (path === "/api/settings/reasoning" && method === "POST") {
        // api() throws the server's bare `detail` (lib/api.ts errorFromResponse)
        if (mode.post === "refuse")
          return Promise.reject(new Error("The team model has no level with that name. Use one of: low, high."));
        mode.saved = JSON.parse(String(opts!.body)).level;
        return mode.postPromise ? mode.postPromise.then(state) : Promise.resolve(state());
      }
      if (path === "/api/settings/reasoning") return Promise.resolve(state());
      if (path === "/api/settings/model" && method === "POST") {
        mode.model = JSON.parse(String(opts!.body)).model;
        return Promise.resolve(pick());
      }
      if (path === "/api/settings/model") return Promise.resolve(pick());
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

const radio = (name: string) => screen.getByRole("radio", { name }) as HTMLInputElement;
const reads = () => requests.filter((r) => r.path === "/api/settings/reasoning" && r.method === "GET").length;
const posts = () => requests.filter((r) => r.path === "/api/settings/reasoning" && r.method === "POST");

beforeEach(() => {
  mode.model = "opus";
  mode.saved = "";
  mode.levels = null;
  mode.post = "ok";
  mode.postPromise = null;
  mode.who = { user: "boss", strong: true, admin: true, can_administer: true, keys_minted: 1 };
  requests.length = 0;
});

describe("the reasoning section", () => {
  it("offers the levels of the team model and saves one", async () => {
    renderSettings();
    const high = await screen.findByRole("radio", { name: "high" }, { timeout: 5_000 });
    expect(radio("Model default").checked).toBe(true);
    expect(radio("low")).toBeTruthy();
    expect(screen.getByRole("radiogroup", { name: "Team reasoning level" })).toBeTruthy();
    const summaryReads = requests.filter((r) => r.path === "/api/settings/model").length;

    fireEvent.click(high);
    await screen.findByText("Saved. Each chat with no level of its own uses it from its next message.");
    expect(posts()).toEqual([
      { path: "/api/settings/reasoning", method: "POST", body: JSON.stringify({ level: "high" }) },
    ]);
    expect(radio("high").checked).toBe(true);
    // the In force summary names the team level, so it is read again
    await waitFor(() =>
      expect(requests.filter((r) => r.path === "/api/settings/model").length).toBeGreaterThan(summaryReads),
    );
    // the refreshed summary must not remount the section and drop the status
    expect(
      screen.getByText("Saved. Each chat with no level of its own uses it from its next message."),
    ).toBeTruthy();
  });

  it("reads the levels once when the page loads", async () => {
    renderSettings();
    await screen.findByRole("radio", { name: "high" }, { timeout: 5_000 });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(reads()).toBe(1);
  });

  it("keeps the pending choice focusable and refuses a second write while one is open", async () => {
    let finish: () => void = () => {};
    mode.postPromise = new Promise<void>((resolve) => {
      finish = resolve;
    });
    renderSettings();
    fireEvent.click(await screen.findByRole("radio", { name: "high" }, { timeout: 5_000 }));
    await screen.findByText("Saving…");
    expect(radio("high").disabled).toBe(false);
    expect(radio("low").disabled).toBe(true);
    fireEvent.click(radio("low"));
    expect(posts()).toHaveLength(1);
    await act(async () => finish());
    await screen.findByText("Saved. Each chat with no level of its own uses it from its next message.");
    expect(radio("low").disabled).toBe(false);
  });

  it("offers the levels of the new team model after the model changes", async () => {
    renderSettings();
    await screen.findByRole("radio", { name: "high" }, { timeout: 5_000 });
    fireEvent.click(screen.getByRole("radio", { name: /^mini/ }));
    await screen.findByRole("radio", { name: "minimal" }, { timeout: 5_000 });
    expect(screen.queryByRole("radio", { name: "high" })).toBeNull();
  });

  it("shows a refusal and keeps the level that is in force", async () => {
    mode.post = "refuse";
    renderSettings();
    fireEvent.click(await screen.findByRole("radio", { name: "high" }, { timeout: 5_000 }));
    await screen.findByText("Not saved. The team model has no level with that name. Use one of: low, high.");
    expect(radio("Model default").checked).toBe(true);
  });

  it("gives a reader without administrator access no working control", async () => {
    mode.who = { user: "ava", strong: false, admin: false, can_administer: false, keys_minted: 0 };
    renderSettings();
    const high = await screen.findByRole("radio", { name: "high" }, { timeout: 5_000 });
    expect((high as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(high);
    expect(posts()).toEqual([]);
  });

  it("clears a saved level the team model stopped offering", async () => {
    mode.saved = "xhigh";
    renderSettings();
    await screen.findByText(
      "The saved level is xhigh. The team model does not offer this level. Chats on the team model use its default. Chats on a model that offers xhigh still use it. Select Model default to clear it.",
      {},
      { timeout: 5_000 },
    );
    // checked against the saved level, so Model default is NOT checked and a
    // click on it is a change the page acts on
    expect(radio("Model default").checked).toBe(false);
    fireEvent.click(radio("Model default"));
    await screen.findByText("Cleared. Each chat with no level of its own uses the default of its model from its next message.");
    expect(posts().at(-1)?.body).toBe(JSON.stringify({ level: "" }));
  });

  it("can clear a saved level when the team model has no levels at all", async () => {
    mode.saved = "high";
    mode.levels = [];
    renderSettings();
    await screen.findByText(/The team model has no reasoning levels\./, {}, { timeout: 5_000 });
    fireEvent.click(radio("Model default"));
    await waitFor(() => expect(posts().at(-1)?.body).toBe(JSON.stringify({ level: "" })));
  });

  it("says where levels come from when the team model has none", async () => {
    mode.levels = [];
    renderSettings();
    await screen.findByText(/The team model has no reasoning levels\./, {}, { timeout: 5_000 });
    expect(screen.queryByRole("radio", { name: "Model default" })).toBeNull();
  });

  it("writes its copy in the house style", async () => {
    mode.saved = "xhigh";
    renderSettings();
    await screen.findByText(/The saved level is xhigh\./, {}, { timeout: 5_000 });
    const text = section().textContent ?? "";
    expect(text).not.toMatch(/\b\w+'(t|s|re|ll|ve|d)\b/);
    expect(text).not.toMatch(/[!;]/);
    expect(text).not.toMatch(/\b(should|would|may)\b/i);
  });
});
