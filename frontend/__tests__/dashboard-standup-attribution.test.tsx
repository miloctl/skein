import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ api: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: mocks.api };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));

import Dashboard from "@/app/dashboard/page";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.api.mockImplementation((path: string) => {
    if (path === "/api/tasks/browse") return Promise.resolve({ open: [], done: [] });
    if (path === "/api/pulse") return Promise.resolve(null);
    if (path === "/api/standups")
      return Promise.resolve([
        {
          id: 1,
          author: "ava",
          created_by: "ava",
          origin: "human",
          yesterday: "planned",
          today: "ship",
          blockers: "",
          visibility: "workspace",
          crew_id: 0,
        },
        {
          id: 2,
          author: "mira",
          created_by: "scout",
          origin: "agent_verified",
          yesterday: "checked",
          today: "report",
          blockers: "",
          visibility: "workspace",
          crew_id: 0,
        },
      ]);
    return Promise.resolve([]);
  });
});

describe("standup authorship", () => {
  it("names the agent writer beside a human byline only for non-human origin", async () => {
    render(<Dashboard />);
    expect(await screen.findByText(/filed by scout/)).toBeTruthy();
    expect(screen.queryByText(/filed by ava/)).toBeNull();
  });
});
