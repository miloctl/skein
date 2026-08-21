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
    maximumCoreExclusive: "0.4.0",
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

describe("runtime manifest validation", () => {
  it("rejects malformed fields before the shell can crash on them", () => {
    // Nav calls activePaths.includes on every render — a packed JavaScript
    // manifest with a bad shape must fail registration with the extension
    // named, never later as a bare TypeError inside the shell.
    expect(() =>
      registerFrontendExtensions([
        extension({
          navigation: [
            {
              id: "atlas.workplace.manager-nav",
              href: "/dashboard",
              label: "Atlas",
              activePaths: "not-an-array" as unknown as string[],
            },
          ],
        }),
      ]),
    ).toThrow(/activePaths/);
    expect(() =>
      registerFrontendExtensions([
        extension({
          navigation: [
            {
              id: "atlas.workplace.manager-nav",
              href: "https://evil.example",
              label: "Atlas",
              activePaths: [],
            },
          ],
        }),
      ]),
    ).toThrow(/application-relative/);
    expect(() =>
      registerFrontendExtensions([
        extension({
          dashboardCards: [
            {
              id: "atlas.workplace.delivery-card",
              slot: "manager-dashboard",
              component: "not a component" as unknown as typeof AtlasCard,
            },
          ],
        }),
      ]),
    ).toThrow(/React component/);
    expect(() =>
      registerFrontendExtensions([
        extension({
          dashboardCards: [
            {
              id: "atlas.workplace.delivery-card",
              slot: "sidebar" as "manager-dashboard",
              component: AtlasCard,
            },
          ],
        }),
      ]),
    ).toThrow(/manager-dashboard/);
  });

  it("caps the composed policy actions at the capability request bound", () => {
    const crowded = extension({
      navigation: Array.from({ length: 65 }, (_ignored, index) => ({
        id: `atlas.workplace.nav-${index}`,
        href: "/dashboard",
        label: `Atlas ${index}`,
        activePaths: [],
        policyAction: `atlas.view.${index}`,
      })),
      dashboardCards: [],
    });
    expect(() => registerFrontendExtensions([crowded])).toThrow(/at most 64/);
  });
});

describe("core namespace and origin hardening", () => {
  it("refuses a policyAction that claims the skein. namespace", () => {
    // The backend capability catalog exempts skein.* (core REST actions are
    // derived, not contributed), so a manifest naming skein.atlas.view
    // rendered with no backend at all through the engine's default permit.
    expect(() =>
      registerFrontendExtensions([
        extension({
          navigation: [
            {
              id: "atlas.workplace.manager-nav",
              href: "/dashboard",
              label: "Atlas",
              activePaths: [],
              policyAction: "skein.atlas.view",
            },
          ],
          dashboardCards: [],
        }),
      ]),
    ).toThrow(/skein\. namespace/);
  });

  it("refuses a backslash href the URL parser would send off-origin", () => {
    // WHATWG normalizes "/\\evil.com" to "//evil.com".
    expect(() =>
      registerFrontendExtensions([
        extension({
          navigation: [
            {
              id: "atlas.workplace.manager-nav",
              href: "/\\evil.com/phish",
              label: "Atlas",
              activePaths: [],
            },
          ],
          dashboardCards: [],
        }),
      ]),
    ).toThrow(/application-relative/);
  });

  it("bounds the encoded capability query, not only the action count", () => {
    // 64 long actions passed the count check, hit the backend's 2,000-char
    // query bound as a 422, and the provider hid every gated contribution
    // with nothing said.
    const long = extension({
      navigation: Array.from({ length: 60 }, (_ignored, index) => ({
        id: `atlas.workplace.nav-${index}`,
        href: "/dashboard",
        label: `Atlas ${index}`,
        activePaths: [],
        policyAction: `atlas.workplace.some.rather.long.action.name.${index}`,
      })),
      dashboardCards: [],
    });
    expect(() => registerFrontendExtensions([long])).toThrow(/characters/);
  });
});
