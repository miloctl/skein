"use client";

import { useEffect, useState } from "react";

import {
  Card,
  type DashboardCardProps,
  type FrontendExtension,
} from "@miloctl/skein-extension-api";

function AtlasDeliveryCard({ api }: DashboardCardProps) {
  const [metrics, setMetrics] = useState<{
    linked_items: number;
    sync_runs: number;
  } | null>(null);
  useEffect(() => {
    api<{ linked_items: number; sync_runs: number }>(
      "/api/extensions/atlas.workplace/metrics",
    )
      .then(setMetrics)
      .catch(() => setMetrics({ linked_items: 0, sync_runs: 0 }));
  }, [api]);
  return (
    <div id="atlas-delivery" className="md:col-span-2">
      <Card title="Atlas delivery indicators">
        {/* mt-[7px] appears nowhere in the core source. It is the canary the
            frontend contract greps out of the built CSS: extension packages
            are Tailwind sources only through the generated @source list, and
            without it this card rendered with its extension-only utilities
            silently missing. */}
        <p className="mt-[7px] text-sm text-ink-2">
          {metrics
            ? `${metrics.linked_items} linked items · ${metrics.sync_runs} sync runs`
            : "Loading Atlas delivery indicators…"}
        </p>
      </Card>
    </div>
  );
}

const extension: FrontendExtension = {
  id: "atlas.workplace",
  version: "2.0.0",
  extensionApi: "1.0",
  minimumCore: "0.3.0",
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
      component: AtlasDeliveryCard,
      policyAction: "atlas.dashboard.view",
    },
  ],
};

export default extension;
