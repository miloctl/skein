import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ api: vi.fn(), deleteFails: false }));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: mocks.api, getUser: () => "tester" };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/agents" }));

import AgentsPage from "@/app/agents/page";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.deleteFails = false;
  mocks.api.mockImplementation((path: string, opts?: { method?: string }) => {
    if (opts?.method)
      return mocks.deleteFails
        ? Promise.reject(new Error("memory store failed"))
        : Promise.resolve({});
    if (path === "/api/memories")
      return Promise.resolve([
        {
          id: 4,
          topic: "launch",
          content: "Ava owns the launch checklist",
          user: "",
        },
      ]);
    if (path === "/api/whoami")
      return Promise.resolve({
        strong: false,
        admin: false,
        can_administer: false,
      });
    if (path === "/api/agents/status")
      return Promise.resolve({
        provider: "mock",
        model: "",
        provider_error: "",
        review_gate: true,
        trust_blocked: "",
        runner_agents: [],
        runner_daily_tokens: 0,
        context_strategy: "sliding",
        context_error: "",
      });
    if (path === "/api/agents/entities")
      return Promise.resolve({ entities: [], always_review: [] });
    return Promise.resolve([]);
  });
});

describe("forgetting a memory", () => {
  it("states what stops using the memory and lets Escape cancel", async () => {
    render(<AgentsPage />);
    const trigger = await screen.findByRole("button", {
      name: "Forget memory: launch",
    });
    fireEvent.click(trigger);

    const confirm = screen.getByRole("button", { name: "Forget memory" });
    const consequence = screen.getByText(
      /It will stop steering agent chats and leave search.*activity record can retain up to 200 characters.*backups can retain the memory/i,
    );
    expect(consequence).toBeTruthy();
    expect(confirm.getAttribute("aria-describedby")).toBe(consequence.id);
    expect(
      mocks.api.mock.calls.filter(([, opts]) => opts?.method === "DELETE"),
    ).toHaveLength(0);

    fireEvent.keyDown(confirm, { key: "Escape" });
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("button", { name: "Forget memory: launch" }),
      ),
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Forget memory: launch" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Forget memory" }));
    await waitFor(() =>
      expect(mocks.api).toHaveBeenCalledWith("/api/memories/4", {
        method: "DELETE",
      }),
    );
  });

  it("keeps a failed forget confirmation open for retry", async () => {
    mocks.deleteFails = true;
    render(<AgentsPage />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Forget memory: launch" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Forget memory" }));

    await waitFor(() =>
      expect(mocks.api).toHaveBeenCalledWith("/api/memories/4", {
        method: "DELETE",
      }),
    );
    expect(screen.getByRole("button", { name: "Forget memory" })).toBeTruthy();
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("button", { name: "Forget memory" }),
      ),
    );
    expect(screen.getByText("Ava owns the launch checklist")).toBeTruthy();
  });
});
