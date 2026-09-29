import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** GitLab is where the team's code lives, and its webhook is the only way
 *  code work reaches Skein: Connections must name its URL and the setting
 *  that opens it. */

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "mira",
    api: (path: string) =>
      path === "/api/whoami"
        ? Promise.resolve({ user: "mira", strong: true, admin: false, can_administer: false, keys_minted: 1 })
        : Promise.reject(new Error("not part of this test")),
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/settings" }));

import SettingsPage from "@/app/settings/page";
import { API_URL } from "@/lib/config";

describe("Settings → Connections", () => {
  it("offers the GitLab webhook URL and names its token setting", async () => {
    window.history.replaceState({}, "", "/settings#settings-connections");
    render(<SettingsPage />);
    expect(await screen.findByRole("heading", { name: "Code forge webhooks (optional)" })).toBeTruthy();
    expect(screen.getByText(`${API_URL}/api/webhooks/gitlab`)).toBeTruthy();
    expect(screen.getByRole("button", { name: /GitLab webhook URL/i })).toBeTruthy();
    expect(screen.getAllByText(/SKEIN_GITLAB_WEBHOOK_TOKEN/).length).toBeGreaterThan(0);
    expect(screen.getByText("skein context --engagement <id> --write AGENTS.md")).toBeTruthy();
  });
});
