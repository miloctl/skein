"use client";

import { useEffect, useState } from "react";

import {
  Card,
  FRONTEND_EXTENSION_API,
  api,
  type FrontendExtension,
} from "@skein/extension-api";

function AtlasDeliveryCard() {
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
  }, []);
  return (
    <div id="atlas-delivery" className="md:col-span-2">
      <Card title="Atlas delivery indicators">
        <p className="text-sm text-ink-2">
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
  version: "1.0.0",
  extensionApi: FRONTEND_EXTENSION_API,
  minimumCore: "0.1.0",
  maximumCoreExclusive: "0.2.0",
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
