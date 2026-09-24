import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ api: vi.fn(), strong: true, weakHeader: false }));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: mocks.api, getUser: () => "tester" };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/agents" }));
vi.mock("@/lib/auth", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/auth")>();
  return { ...real, trustedHeaderIdentity: () => mocks.weakHeader };
});

import AgentsPage from "@/app/agents/page";
import { getStatus } from "@/lib/status";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.api.mockImplementation((path: string, opts?: { method?: string }) => {
    if (opts?.method === "POST") return Promise.resolve({ id: 12, status: "pending" });
    if (path === "/api/memories")
      return Promise.resolve([
        { id: 4, topic: "launch", content: "Ava owns the launch checklist", user: "" },
        { id: 7, topic: "focus", content: "I review PRs after lunch", user: "tester" },
      ]);
    if (path === "/api/whoami")
      return Promise.resolve({ strong: mocks.strong, admin: false, can_administer: false });
    if (path === "/api/agents/entities")
      return Promise.resolve({ entities: [], always_review: [] });
    return Promise.resolve([]);
  });
});

describe("sharing a memory with the team", () => {
  it("offers the share on your own memories and files a proposal", async () => {
    mocks.strong = true;
    render(<AgentsPage />);
    const share = await screen.findByRole("button", {
      name: "Share memory with the team: focus",
    });
    expect(screen.queryByRole("button", { name: "Share memory with the team: launch" })).toBeNull();
    expect(screen.getByText("(only you)")).toBeTruthy();
    fireEvent.click(share);
    await waitFor(() =>
      expect(mocks.api).toHaveBeenCalledWith("/api/memories/7/share", { method: "POST" }),
    );
    await waitFor(() => expect(getStatus()?.message).toMatch(/^Filed as proposal #12\./));
    // the share waits for a teammate: no second button to click, and focus
    // lands on the line that says so
    const waiting = await screen.findByText("waiting for a teammate");
    expect(screen.queryByRole("button", { name: "Share memory with the team: focus" })).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(waiting));
  });

  it("does not offer it to a weak identity, which the route refuses", async () => {
    mocks.strong = false;
    render(<AgentsPage />);
    await screen.findByRole("button", { name: "Forget memory: focus" });
    expect(screen.queryByRole("button", { name: /Share memory with the team/ })).toBeNull();
  });

  it("tells a trusted-header name with no key that anyone can read its memories", async () => {
    mocks.strong = false;
    mocks.weakHeader = true;
    try {
      render(<AgentsPage />);
      await screen.findByRole("button", { name: "Forget memory: focus" });
      expect(
        screen.getByText(/Anyone who can reach this server can pick your name and read this\./),
      ).toBeTruthy();
    } finally {
      mocks.weakHeader = false;
    }
  });
});

