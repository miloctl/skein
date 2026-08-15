import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => {
      if (path === "/api/agents")
        return Promise.resolve([
          { agent: "agent", delegatable: true },
          { agent: "mcp-agent", delegatable: false },
        ]);
      if (path.endsWith("/worklog")) return Promise.resolve([]);
      return Promise.resolve({
        id: 4,
        title: "Build the happy path",
        status: "todo",
        priority: "high",
      });
    },
  };
});

import { TaskPeek } from "@/components/task-peek";

describe("task delegation options", () => {
  it("shows only identities the backend marks as delegatable", async () => {
    window.history.pushState({}, "", "?task=4");
    render(<TaskPeek />);

    await waitFor(() => expect(screen.getByRole("option", { name: "agent" })).toBeTruthy());
    expect(screen.queryByRole("option", { name: "mcp-agent" })).toBeNull();
  });
});
