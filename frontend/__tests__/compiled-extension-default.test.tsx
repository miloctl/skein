import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { ExtensionDashboardCards } from "@/components/extension-dashboard";
import { ExtensionProvider, useFrontendExtensions } from "@/lib/extensions/context";

vi.mock("@/extensions/generated", async () => {
  const React = await import("react");
  return {
    compiledExtensions: [
      {
        id: "atlas.workplace",
        version: "2.0.0",
        extensionApi: "1.0",
        minimumCore: "0.3.0",
        maximumCoreExclusive: "0.5.0",
        navigation: [
          {
            id: "atlas.workplace.manager-nav",
            href: "/dashboard#atlas-delivery",
            label: "Atlas",
            activePaths: ["/dashboard"],
          },
        ],
        dashboardCards: [
          {
            id: "atlas.workplace.delivery-card",
            slot: "manager-dashboard",
            component: () => React.createElement("p", null, "Compiled Atlas card"),
          },
        ],
      },
    ],
  };
});

vi.mock("@/lib/api", () => ({ api: () => Promise.resolve({ actions: {} }) }));

function NavigationProbe() {
  const { navigation } = useFrontendExtensions();
  return <p>{navigation.map((item) => item.label).join(",") || "hidden"}</p>;
}

it("uses the compiled extension registry by default", async () => {
  render(
    <ExtensionProvider>
      <NavigationProbe />
      <ExtensionDashboardCards />
    </ExtensionProvider>,
  );

  expect(await screen.findByText("Atlas")).toBeTruthy();
  expect(screen.getByText("Compiled Atlas card")).toBeTruthy();
});
