"use client";

import { createElement, useEffect, useState } from "react";

import { Card, FRONTEND_EXTENSION_API, api } from "@skein/extension-api";

function AtlasDeliveryCard() {
  const [metrics, setMetrics] = useState(null);
  useEffect(() => {
    api("/api/extensions/atlas.workplace/metrics")
      .then(setMetrics)
      .catch(() => setMetrics({ linked_items: 0, sync_runs: 0 }));
  }, []);
  const message = metrics
    ? `${metrics.linked_items} linked items · ${metrics.sync_runs} sync runs`
    : "Loading Atlas delivery indicators…";
  return createElement(
    "div",
    { id: "atlas-delivery", className: "md:col-span-2" },
    createElement(
      Card,
      { title: "Atlas delivery indicators" },
      createElement("p", { className: "text-sm text-ink-2" }, message),
    ),
  );
}

const extension = {
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
