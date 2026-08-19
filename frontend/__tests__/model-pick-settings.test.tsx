import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
    {
      id: "provider",
      label: "Provider",
      value: "anthropic",
      source: "SKEIN_MODEL_PROVIDER",
    },
    {
      id: "model",
      label: "Team-default model",
      value: "opus",
      source: "Settings → AI runtime → Model (team)",
    },
    {
      id: "output_cap",
      label: "Output cap",
      value: "8,192 tokens",
      source: "selected model entry",
    },
    {
      id: "attachments",
      label: "Attachments",
      value: "Direct: document. Images: vision sidecar.",
      source: "selected model entry + SKEIN_VISION_MODEL",
    },
    {
      id: "vision_sidecar",
      label: "Vision sidecar",
      value: "qwen3.5:cloud",
      source: "SKEIN_VISION_MODEL",
    },
    {
      id: "long_chat",
      label: "Long chats",
      value: "sliding",
      source: "SKEIN_CONTEXT_STRATEGY",
    },
    {
      id: "model_menu",
      label: "Model menu",
      value: "2 models",
      source: "SKEIN_MODELS_FILE",
    },
    {
      id: "prices",
      label: "Prices",
      value: "Set for team-default model",
      source: "selected model entry",
    },
    {
      id: "parameters",
      label: "Parameters",
      value: "3 parameters",
      source: "SKEIN_MODEL_PARAMS_FILE + selected model entry",
    },
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

const requests: { path: string; method: string }[] = [];
const mode: {
  pick: unknown;
  post: "ok" | "refuse";
  postPromise: Promise<unknown> | null;
  contextPostPromise: Promise<unknown> | null;
  pickQueue: Promise<unknown>[];
  who: {
    user: string;
    strong: boolean;
    admin: boolean;
    can_administer: boolean;
    keys_minted: number;
  };
  context: {
    strategy: string;
    override: string;
    default: string;
    choices: string[];
    applies: boolean;
  };
} = {
  pick: PICK,
  post: "ok",
  postPromise: null,
  contextPostPromise: null,
  pickQueue: [],
  who: {
    user: "boss",
    strong: true,
    admin: true,
    can_administer: true,
    keys_minted: 1,
  },
  context: {
    strategy: "sliding",
    override: "",
    default: "sliding",
    choices: ["sliding", "summarize"],
    applies: true,
  },
};
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, opts?: RequestInit) => {
      requests.push({ path, method: opts?.method ?? "GET" });
      if (path === "/api/settings/model" && opts?.method === "POST") {
        if (mode.postPromise) return mode.postPromise;
        return mode.post === "ok"
          ? Promise.resolve(mode.pick)
          : Promise.reject(
              new Error(
                "HTTP 400: unknown model — expected one of: mini, opus",
              ),
            );
      }
      if (path === "/api/settings/model")
        return mode.pickQueue.shift() ?? Promise.resolve(mode.pick);
      if (
        path === "/api/settings/context-strategy" &&
        opts?.method === "POST"
      ) {
        const strategy = JSON.parse(String(opts.body)).strategy;
        mode.context = { ...mode.context, strategy, override: strategy };
        mode.pick = {
          ...(mode.pick as typeof PICK),
          summary: {
            ...SUMMARY,
            rows: SUMMARY.rows.map((row) =>
              row.id === "long_chat" ? { ...row, value: strategy } : row,
            ),
          },
        };
        return mode.contextPostPromise ?? Promise.resolve(mode.context);
      }
      if (path === "/api/settings/context-strategy")
        return Promise.resolve(mode.context);
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

beforeEach(() => {
  vi.useRealTimers();
  mode.pick = PICK;
  mode.post = "ok";
  mode.postPromise = null;
  mode.contextPostPromise = null;
  mode.pickQueue.length = 0;
  mode.who = {
    user: "boss",
    strong: true,
    admin: true,
    can_administer: true,
    keys_minted: 1,
  };
  mode.context = {
    strategy: "sliding",
    override: "",
    default: "sliding",
    choices: ["sliding", "summarize"],
    applies: true,
  };
  requests.length = 0;
});

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
    const radio = await screen.findByRole("radio", {
      name: /Opus — deep work/,
    });
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
    expect(
      await screen.findByText("Set in parameters (value hidden)"),
    ).toBeTruthy();
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

  it("announces a clear after the stale picker no longer needs controls", async () => {
    mode.pick = {
      ...PICK,
      model: "env-default",
      ignored: "The picked model is no longer in the menu.",
      menu: [],
      applies: false,
    };
    render(<SettingsPage />);
    const clear = await screen.findByRole("button", {
      name: /Use the deployment default/,
    });
    mode.pickQueue.push(
      Promise.resolve({
        ...(mode.pick as typeof PICK),
        override: null,
        ignored: "",
      }),
    );
    fireEvent.click(clear);
    expect(
      await screen.findByText(
        "Cleared. Back to the deployment default (env-default).",
      ),
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
    const radios = [
      screen.getByRole("radio", { name: /Opus — deep work/ }),
      screen.getByRole("radio", { name: /mini/ }),
    ];
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
    await screen.findByText(
      /Saved\. Every chat uses it from its next message\./,
    );
  });

  it("serializes a model write through its refresh without disabling focus", async () => {
    let finishPost!: (value: unknown) => void;
    let finishRefresh!: (value: unknown) => void;
    mode.postPromise = new Promise((resolve) => {
      finishPost = resolve;
    });
    render(<SettingsPage />);
    const radio = await screen.findByRole("radio", { name: /mini/ });
    mode.pickQueue.push(
      new Promise((resolve) => {
        finishRefresh = resolve;
      }),
    );

    radio.focus();
    fireEvent.click(radio);
    fireEvent.click(radio);

    expect(
      requests.filter(
        (request) =>
          request.path === "/api/settings/model" && request.method === "POST",
      ),
    ).toHaveLength(1);
    expect((radio as HTMLInputElement).disabled).toBe(false);
    expect(
      (
        screen.getByRole("radio", {
          name: /Opus — deep work/,
        }) as HTMLInputElement
      ).disabled,
    ).toBe(true);
    expect(radio).toBe(document.activeElement);
    expect(radio.closest("[aria-busy]")?.getAttribute("aria-busy")).toBe(
      "true",
    );
    expect(screen.getByText("Saving…")).toBeTruthy();

    finishPost(mode.pick);
    await waitFor(() =>
      expect(
        requests.filter(
          (request) =>
            request.path === "/api/settings/model" && request.method === "GET",
        ),
      ).toHaveLength(2),
    );
    fireEvent.click(radio);
    expect(
      requests.filter(
        (request) =>
          request.path === "/api/settings/model" && request.method === "POST",
      ),
    ).toHaveLength(1);

    finishRefresh({ ...PICK, model: "mini" });
    await screen.findByText("Saved. Every chat uses it from its next message.");
    expect(radio.closest("[aria-busy]")?.getAttribute("aria-busy")).toBe(
      "false",
    );
  });

  it("releases a model write when its result stays unknown", async () => {
    mode.postPromise = new Promise(() => {});
    render(<SettingsPage />);
    const radio = await screen.findByRole("radio", { name: /mini/ });
    vi.useFakeTimers();

    fireEvent.click(radio);
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    expect(
      screen.getByText(
        "The save timed out. The result is unknown. Check the current setting before you try again.",
      ),
    ).toBeTruthy();
    expect(radio.closest("[aria-busy]")?.getAttribute("aria-busy")).toBe(
      "false",
    );
  });

  it("states when a new identity must retry an active write", async () => {
    let finishPost!: (value: unknown) => void;
    mode.postPromise = new Promise((resolve) => {
      finishPost = resolve;
    });
    render(<SettingsPage />);
    fireEvent.click(await screen.findByRole("radio", { name: /mini/ }));
    expect(
      requests.filter(
        (request) =>
          request.path === "/api/settings/model" && request.method === "POST",
      ),
    ).toHaveLength(1);

    mode.who = { ...mode.who, user: "next-boss" };
    window.dispatchEvent(new Event("storage"));
    await screen.findByText(/strong identity active as next-boss/);
    await waitFor(() =>
      expect(
        screen.getByRole("radio", { name: /Opus — deep work/ }),
      ).toHaveProperty("checked", true),
    );

    const nextRadio = screen.getByRole("radio", { name: /mini/ });
    fireEvent.click(nextRadio);
    expect(
      requests.filter(
        (request) =>
          request.path === "/api/settings/model" && request.method === "POST",
      ),
    ).toHaveLength(1);
    expect(
      screen.getByText(
        "A model change is still saving. When it finishes, try again.",
      ),
    ).toBeTruthy();

    mode.postPromise = null;
    finishPost(mode.pick);
    await waitFor(() =>
      expect(nextRadio.closest("[aria-busy]")?.getAttribute("aria-busy")).toBe(
        "false",
      ),
    );
    fireEvent.click(nextRadio);
    await waitFor(() =>
      expect(
        requests.filter(
          (request) =>
            request.path === "/api/settings/model" && request.method === "POST",
        ),
      ).toHaveLength(2),
    );
    expect(
      await screen.findByText(
        "Saved. Every chat uses it from its next message.",
      ),
    ).toBeTruthy();
  });

  it("serializes a long-chat write through both refreshes", async () => {
    let finishPost!: (value: unknown) => void;
    mode.contextPostPromise = new Promise((resolve) => {
      finishPost = resolve;
    });
    render(<SettingsPage />);
    const radio = await screen.findByRole("radio", {
      name: /Summarize the oldest messages/,
    });

    radio.focus();
    fireEvent.click(radio);
    fireEvent.click(radio);
    expect(
      requests.filter(
        (request) =>
          request.path === "/api/settings/context-strategy" &&
          request.method === "POST",
      ),
    ).toHaveLength(1);
    expect((radio as HTMLInputElement).disabled).toBe(false);
    expect(radio).toBe(document.activeElement);
    expect(radio.closest("[aria-busy]")?.getAttribute("aria-busy")).toBe(
      "true",
    );
    expect(screen.getByText("Saving…")).toBeTruthy();

    finishPost(mode.context);
    expect(
      await screen.findByText(
        "Saved. Every chat uses it from its next message.",
      ),
    ).toBeTruthy();
    expect(radio.closest("[aria-busy]")?.getAttribute("aria-busy")).toBe(
      "false",
    );
  });

  it("refreshes the model summary after the long-chat strategy changes", async () => {
    render(<SettingsPage />);
    await screen.findByText("sliding");
    requests.length = 0;

    fireEvent.click(
      await screen.findByRole("radio", {
        name: /Summarize the oldest messages/,
      }),
    );

    await waitFor(() =>
      expect(requests).toContainEqual({
        path: "/api/settings/model",
        method: "GET",
      }),
    );
    expect(await screen.findByText("summarize")).toBeTruthy();
  });

  it("keeps the newest model summary when refreshes finish out of order", async () => {
    let finishOlder!: (value: unknown) => void;
    let finishNewer!: (value: unknown) => void;
    render(<SettingsPage />);
    await screen.findByText("sliding");
    mode.pickQueue.push(
      new Promise((resolve) => {
        finishOlder = resolve;
      }),
      new Promise((resolve) => {
        finishNewer = resolve;
      }),
    );
    requests.length = 0;

    fireEvent.click(
      screen.getByRole("radio", { name: /Summarize the oldest messages/ }),
    );
    await waitFor(() =>
      expect(
        requests.filter(
          (request) =>
            request.path === "/api/settings/model" && request.method === "GET",
        ),
      ).toHaveLength(1),
    );
    fireEvent.click(screen.getByRole("radio", { name: /mini/ }));
    await waitFor(() =>
      expect(
        requests.filter(
          (request) =>
            request.path === "/api/settings/model" && request.method === "GET",
        ),
      ).toHaveLength(2),
    );

    const summaryWith = (value: string) => ({
      ...PICK,
      summary: {
        ...SUMMARY,
        rows: SUMMARY.rows.map((row) =>
          row.id === "model" ? { ...row, value } : row,
        ),
      },
    });
    finishNewer(summaryWith("newest-model"));
    expect(await screen.findByText("newest-model")).toBeTruthy();
    finishOlder(summaryWith("stale-model"));
    await waitFor(() => expect(screen.queryByText("stale-model")).toBeNull());
    expect(screen.getByText("newest-model")).toBeTruthy();
  });

  it("shows the server's registry fault instead of a menu", async () => {
    mode.pick = {
      ...PICK,
      applies: false,
      menu: [],
      menu_error:
        "SKEIN_MODELS is unusable: entry 1 has no usable id. The model menu is off.",
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
        screen.getAllByText(
          /No model is connected\. This setting is not in use\./,
        ).length,
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
