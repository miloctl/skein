"use client";

import { useFrontendExtensions } from "@/lib/extensions/context";
import { api } from "@/lib/api";

export function ExtensionDashboardCards() {
  const { dashboardCards } = useFrontendExtensions();
  return dashboardCards
    .filter((contribution) => contribution.slot === "manager-dashboard")
    .map((contribution) => {
      const Component = contribution.component;
      return (
        <Component
          key={contribution.id}
          extensionId={contribution.extensionId}
          api={api}
        />
      );
    });
}
