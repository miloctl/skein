import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ExtensionDashboardCards } from "@/components/extension-dashboard";
import { ExtensionProvider, useFrontendExtensions } from "@/lib/extensions/context";
import {
  FRONTEND_EXTENSION_API,
  type FrontendExtension,
} from "@/lib/extensions/contracts";
import { registerFrontendExtensions } from "@/lib/extensions/registry";

const capability = vi.hoisted(() => ({ effect: "permit" }));

vi.mock("@/lib/api", () => ({
  api: (path: string) => {
    if (!path.startsWith("/api/capabilities")) return Promise.resolve({});
    return Promise.resolve({
      subject: "manager",
      roles: ["delivery-manager"],
      capabilities: ["atlas.dashboard"],
      actions: {
        "atlas.dashboard.view": { effect: capability.effect },
      },
    });
  },
}));

function AtlasCard() {
  return <section aria-label="Atlas delivery">Atlas delivery indicators</section>;
}

function extension(changes: Partial<FrontendExtension> = {}): FrontendExtension {
  return {
    id: "atlas.workplace",
    version: "1.0.0",
    extensionApi: FRONTEND_EXTENSION_API,
    minimumCore: "0.2.0",
    maximumCoreExclusive: "0.3.0",
    navigation: [
      {
        id: "atlas.workplace.manager-nav",
        href: "/dashboard#atlas-delivery",
        label: "Atlas",
        activePaths: ["/dashboard"],
        policyAction: "atlas.dashboard.view",
      },
    ],
    dashboardCards: [
      {
        id: "atlas.workplace.delivery-card",
        slot: "manager-dashboard",
        component: AtlasCard,
        policyAction: "atlas.dashboard.view",
      },
    ],
    ...changes,
  };
}

function NavigationProbe() {
  const { navigation } = useFrontendExtensions();
  return <p>{navigation.map((item) => item.label).join(",") || "hidden"}</p>;
}

beforeEach(() => {
  capability.effect = "permit";
});

describe("the frontend extension registry", () => {
  it("rejects duplicate modules and contribution names", () => {
    expect(() => registerFrontendExtensions([extension(), extension()])).toThrow(
      /Duplicate frontend extension id/,
    );
    expect(() =>
      registerFrontendExtensions([
        extension({
          dashboardCards: [
            {
              id: "wrong.card",
              slot: "manager-dashboard",
              component: AtlasCard,
            },
          ],
        }),
      ]),
    ).toThrow(/namespace/);
  });

  it("rejects incompatible core and extension API versions", () => {
    expect(() =>
      registerFrontendExtensions([
        extension({ maximumCoreExclusive: "0.2.0" }),
      ]),
    ).toThrow(/does not support/);
    expect(() =>
      registerFrontendExtensions([
        extension({ extensionApi: "2.0" as "1.0" }),
      ]),
    ).toThrow(/extension API/);
  });
});

describe("capability-aware contributions", () => {
  it("shows navigation and a dashboard card after a backend permit", async () => {
    render(
      <ExtensionProvider extensions={[extension()]}>
        <NavigationProbe />
        <ExtensionDashboardCards />
      </ExtensionProvider>,
    );
    expect(screen.getByText("hidden")).toBeTruthy();
    expect(await screen.findByText("Atlas")).toBeTruthy();
    expect(screen.getByText("Atlas delivery indicators")).toBeTruthy();
  });

  it("fails closed when the backend denies the policy action", async () => {
    capability.effect = "deny";
    render(
      <ExtensionProvider extensions={[extension()]}>
        <NavigationProbe />
        <ExtensionDashboardCards />
      </ExtensionProvider>,
    );
    await waitFor(() => expect(screen.getByText("hidden")).toBeTruthy());
    expect(screen.queryByText("Atlas delivery indicators")).toBeNull();
  });

  it("refreshes visible contributions when the active identity changes", async () => {
    render(
      <ExtensionProvider extensions={[extension()]}>
        <NavigationProbe />
        <ExtensionDashboardCards />
      </ExtensionProvider>,
    );
    expect(await screen.findByText("Atlas")).toBeTruthy();

    capability.effect = "deny";
    window.dispatchEvent(new Event("storage"));
    await waitFor(() => expect(screen.getByText("hidden")).toBeTruthy());
    expect(screen.queryByText("Atlas delivery indicators")).toBeNull();

    capability.effect = "permit";
    window.dispatchEvent(new Event("storage"));
    expect(await screen.findByText("Atlas")).toBeTruthy();
    expect(screen.getByText("Atlas delivery indicators")).toBeTruthy();
  });
});
