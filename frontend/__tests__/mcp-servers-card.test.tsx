import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** The card lists the operator's servers by name only, the reader's own with
 *  their URL, adds through POST with the typed fields, and deletes only after
 *  an explicit confirm that states the consequence. */

type Answer = "pending" | "fail" | (() => unknown);
const mode: { answer: Answer; write: "ok" | "fail" } = {
  answer: "pending",
  write: "ok",
};
const writes: { path: string; init?: RequestInit }[] = [];

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: RequestInit) => {
      if (init?.method && init.method !== "GET") {
        writes.push({ path, init });
        if (path.endsWith("/sign-in"))
          return Promise.resolve({ authorization_url: "https://idp.example/authorize?state=s" });
        return mode.write === "ok"
          ? Promise.resolve({
              id: 9,
              name: "jira",
              url: "https://jira.example/mcp",
              auth: "token",
              has_token: true,
              signed_in: false,
              server_id: "personal:ava:jira",
              status: { connected: false, offered: 0, retry_in_seconds: 30, tools: [] },
            })
          : Promise.reject(
              new Error("A credential cannot be stored: SKEIN_CREDENTIAL_KEY is not set."),
            );
      }
      return mode.answer === "pending"
        ? new Promise(() => {})
        : mode.answer === "fail"
          ? Promise.reject(new Error("mcp service exploded"))
          : Promise.resolve(mode.answer());
    },
  };
});

import { McpServersCard } from "@/components/mcp-servers-card";

const PAYLOAD = {
  sealing: true,
  system: [
    {
      server_id: "github",
      tier: "system",
      connected: true,
      offered: 7,
      retry_in_seconds: null,
      tools: [{ name: "search_issues", effect: "read", risk: "low" }],
    },
  ],
  personal: [
    {
      id: 4,
      name: "notes",
      url: "https://notes.example/mcp",
      auth: "token",
      has_token: false,
      signed_in: false,
      server_id: "personal:ava:notes",
      status: {
        server_id: "personal:ava:notes",
        tier: "personal",
        connected: true,
        offered: 2,
        retry_in_seconds: null,
        tools: [{ name: "notes_write", effect: "write", risk: "high" }],
      },
    },
  ],
};

beforeEach(() => {
  mode.answer = "pending";
  mode.write = "ok";
  writes.length = 0;
});

describe("McpServersCard", () => {
  it("requires strong identity and fetches nothing without it", () => {
    render(<McpServersCard strong={false} />);
    expect(screen.getByText(/requires strong identity/)).toBeTruthy();
    expect(screen.queryByText("Loading…")).toBeNull();
  });

  it("shows system servers by name and health, and own servers with their URL", async () => {
    mode.answer = () => PAYLOAD;
    render(<McpServersCard strong />);
    await screen.findByText("github");
    expect(screen.getByText("connected, 1 of 7 tools governed")).toBeTruthy();
    expect(screen.getByText("https://notes.example/mcp")).toBeTruthy();
    expect(screen.getByText("notes_write · write")).toBeTruthy();
    expect(screen.queryByText(/github\.example/)).toBeNull();
  });

  it("adds a server with the typed name, URL and token", async () => {
    mode.answer = () => PAYLOAD;
    render(<McpServersCard strong />);
    await screen.findByText("github");
    fireEvent.change(screen.getByLabelText("Server name"), { target: { value: "jira" } });
    fireEvent.change(screen.getByLabelText("Server URL"), {
      target: { value: "https://jira.example/mcp" },
    });
    fireEvent.change(screen.getByLabelText("Bearer token (optional)"), {
      target: { value: "secret-token" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add server" }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].path).toBe("/api/mcp/servers");
    expect(JSON.parse(String(writes[0].init?.body))).toEqual({
      name: "jira",
      url: "https://jira.example/mcp",
      auth_token: "secret-token",
      auth: "token",
    });
    await waitFor(() =>
      expect((screen.getByLabelText("Server name") as HTMLInputElement).value).toBe(""),
    );
  });

  it("keeps the typed fields when the write is refused", async () => {
    mode.answer = () => PAYLOAD;
    mode.write = "fail";
    render(<McpServersCard strong />);
    await screen.findByText("github");
    fireEvent.change(screen.getByLabelText("Server name"), { target: { value: "jira" } });
    fireEvent.change(screen.getByLabelText("Server URL"), {
      target: { value: "https://jira.example/mcp" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add server" }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect((screen.getByLabelText("Server name") as HTMLInputElement).value).toBe("jira");
  });

  it("disables the token field and says which variable is missing when sealing is off", async () => {
    mode.answer = () => ({ ...PAYLOAD, sealing: false });
    render(<McpServersCard strong />);
    await screen.findByText("github");
    expect((screen.getByLabelText("Bearer token (optional)") as HTMLInputElement).disabled).toBe(
      true,
    );
    expect(screen.getByText(/SKEIN_CREDENTIAL_KEY is not set/)).toBeTruthy();
  });

  it("deletes only after a confirm that states the consequence", async () => {
    mode.answer = () => PAYLOAD;
    render(<McpServersCard strong />);
    await screen.findByText("notes");
    fireEvent.click(screen.getByRole("button", { name: "Delete server notes" }));
    expect(writes).toHaveLength(0);
    expect(screen.getByText(/stored token is deleted/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Confirm: delete server notes" }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].path).toBe("/api/mcp/servers/4");
    expect(writes[0].init?.method).toBe("DELETE");
  });

  it("adds an OAuth server without a token and starts its sign-in in a new tab", async () => {
    const opened: string[] = [];
    vi.stubGlobal("open", (url: string) => {
      opened.push(url);
      return null;
    });
    mode.answer = () => ({
      ...PAYLOAD,
      personal: [
        {
          id: 5,
          name: "jira",
          url: "https://jira.example/mcp",
          auth: "oauth",
          has_token: false,
          signed_in: false,
          sign_in_required: true,
          server_id: "personal:ava:jira",
          status: null,
        },
      ],
    });
    render(<McpServersCard strong />);
    await screen.findByText("sign-in needed");
    fireEvent.click(screen.getByLabelText("sign in with OAuth"));
    expect((screen.getByLabelText("Bearer token (optional)") as HTMLInputElement).disabled).toBe(
      true,
    );
    fireEvent.change(screen.getByLabelText("Server name"), { target: { value: "gh" } });
    fireEvent.change(screen.getByLabelText("Server URL"), {
      target: { value: "https://gh.example/mcp" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add server" }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(JSON.parse(String(writes[0].init?.body))).toEqual({
      name: "gh",
      url: "https://gh.example/mcp",
      auth_token: "",
      auth: "oauth",
    });

    writes.length = 0;
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].path).toBe("/api/mcp/servers/5/sign-in");
    await waitFor(() => expect(opened).toHaveLength(1));
    vi.unstubAllGlobals();
  });

  it("reports a failed load on the card, not the page", async () => {
    mode.answer = "fail";
    render(<McpServersCard strong />);
    expect((await screen.findByRole("status")).textContent).toContain(
      "Cannot load remote MCP servers.",
    );
  });
});
