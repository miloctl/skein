"use client";
import { jsx as _jsx } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { Card, } from "@skein/extension-api";
function AtlasDeliveryCard({ api }) {
    const [metrics, setMetrics] = useState(null);
    useEffect(() => {
        api("/api/extensions/atlas.workplace/metrics")
            .then(setMetrics)
            .catch(() => setMetrics({ linked_items: 0, sync_runs: 0 }));
    }, [api]);
    return (_jsx("div", { id: "atlas-delivery", className: "md:col-span-2", children: _jsx(Card, { title: "Atlas delivery indicators", children: _jsx("p", { className: "mt-[7px] text-sm text-ink-2", children: metrics
                    ? `${metrics.linked_items} linked items · ${metrics.sync_runs} sync runs`
                    : "Loading Atlas delivery indicators…" }) }) }));
}
const extension = {
    id: "atlas.workplace",
    version: "1.0.0",
    extensionApi: "1.0",
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
            component: AtlasDeliveryCard,
            policyAction: "atlas.dashboard.view",
        },
    ],
};
export default extension;
