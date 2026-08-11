import type { ComponentType } from "react";

export const FRONTEND_EXTENSION_API = "1.0";
export const SKEIN_FRONTEND_VERSION = "0.1.0";

type NavigationContribution = {
  id: string;
  href: string;
  label: string;
  activePaths: string[];
  policyAction?: string;
};

export type DashboardCardProps = {
  extensionId: string;
};

type DashboardCardContribution = {
  id: string;
  slot: "manager-dashboard";
  component: ComponentType<DashboardCardProps>;
  policyAction?: string;
};

export type FrontendExtension = {
  id: string;
  version: string;
  extensionApi: typeof FRONTEND_EXTENSION_API;
  minimumCore: string;
  maximumCoreExclusive: string;
  navigation?: NavigationContribution[];
  dashboardCards?: DashboardCardContribution[];
};

export type FrontendExtensionRegistry = {
  extensions: readonly FrontendExtension[];
  navigation: readonly (NavigationContribution & { extensionId: string })[];
  dashboardCards: readonly (DashboardCardContribution & {
    extensionId: string;
  })[];
  policyActions: readonly string[];
};
