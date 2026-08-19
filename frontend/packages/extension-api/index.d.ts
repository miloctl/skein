import type { ComponentType, ReactNode } from "react";

export declare const FRONTEND_EXTENSION_API: "1.0";

export declare function Card(props: {
  title?: string;
  className?: string;
  titleClassName?: string;
  headingLevel?: 1 | 2 | 3 | 4 | 5 | 6;
  children: ReactNode;
}): ReactNode;

export declare function EmptyState(props: { children: ReactNode }): ReactNode;

export type ExtensionApiClient = <T = unknown>(
  path: string,
  init?: RequestInit,
) => Promise<T>;

export type DashboardCardProps = {
  extensionId: string;
  api: ExtensionApiClient;
};

export type NavigationContribution = {
  id: string;
  href: string;
  label: string;
  activePaths: string[];
  policyAction?: string;
};

export type DashboardCardContribution = {
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
