import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { ExtensionDashboardCards } from "@/components/extension-dashboard";
import { ExtensionProvider, useFrontendExtensions } from "@/lib/extensions/context";

vi.mock("@/lib/api", () => ({
  api: (path: string) => {
    if (path.startsWith("/api/capabilities")) {
      return Promise.resolve({
        subject: "manager",
        roles: ["delivery-manager"],
        capabilities: ["atlas.dashboard"],
        actions: { "atlas.dashboard.view": { effect: "permit" } },
      });
    }
    if (path === "/api/extensions/atlas.workplace/metrics") {
      return Promise.resolve({ linked_items: 7, sync_runs: 3 });
    }
    return Promise.reject(new Error("unexpected API request"));
  },
}));

function NavigationProbe() {
  const { navigation } = useFrontendExtensions();
  return <p>{navigation.map((item) => item.label).join(",") || "hidden"}</p>;
}

it("renders the packed Atlas extension through the host registry", async () => {
  render(
    <ExtensionProvider>
      <NavigationProbe />
      <ExtensionDashboardCards />
    </ExtensionProvider>,
  );

  expect(await screen.findByText("Atlas")).toBeTruthy();
  expect(await screen.findByText("7 linked items · 3 sync runs")).toBeTruthy();
});
