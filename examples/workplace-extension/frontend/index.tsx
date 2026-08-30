"use client";

import { useEffect, useState } from "react";

import {
  Card,
  type DashboardCardProps,
  type FrontendExtension,
} from "@miloctl/skein-extension-api";

type MetricsState =
  | { status: "loading" }
  | { status: "ready"; linkedItems: number; syncRuns: number }
  | { status: "unavailable" };

function AtlasDeliveryCard({ api }: DashboardCardProps) {
  const [metrics, setMetrics] = useState<MetricsState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    setMetrics({ status: "loading" });
    api<{ linked_items: number; sync_runs: number }>(
      "/api/extensions/atlas.workplace/metrics",
    ).then(
      (value) => {
        if (active)
          setMetrics({
            status: "ready",
            linkedItems: value.linked_items,
            syncRuns: value.sync_runs,
          });
      },
      () => {
        if (active) setMetrics({ status: "unavailable" });
      },
    );
    return () => {
      active = false;
    };
  }, [api, attempt]);
  return (
    <div id="atlas-delivery" className="md:col-span-2">
      <Card title="Atlas delivery indicators">
        {/* mt-[7px] appears nowhere in the core source. It is the canary the
            frontend contract greps out of the built CSS: extension packages
            are Tailwind sources only through the generated @source list, and
            without it this card rendered with its extension-only utilities
            silently missing. */}
        <div className="mt-[7px] text-sm text-ink-2">
          <p
            role={metrics.status === "unavailable" ? "alert" : "status"}
            aria-live="polite"
          >
            {metrics.status === "loading"
              ? "Loading Atlas delivery indicators…"
              : metrics.status === "ready"
                ? `${metrics.linkedItems} linked ${metrics.linkedItems === 1 ? "item" : "items"} · ${metrics.syncRuns} sync ${metrics.syncRuns === 1 ? "run" : "runs"}`
                : "Atlas delivery indicators are unavailable."}
          </p>
          {metrics.status === "unavailable" ? (
            <button
              type="button"
              className="mt-2 underline hover:text-ink"
              onClick={() => setAttempt((value) => value + 1)}
            >
              Try again
            </button>
          ) : null}
        </div>
      </Card>
    </div>
  );
}

const extension: FrontendExtension = {
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
