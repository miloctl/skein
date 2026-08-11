"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { api } from "@/lib/api";
import { compiledExtensions } from "@/extensions/generated";
import type { FrontendExtension, FrontendExtensionRegistry } from "./contracts";
import { registerFrontendExtensions } from "./registry";

type Decision = { effect: "permit" | "deny" | "review" };
type CapabilityResponse = { actions: Record<string, Decision> };

const EMPTY = registerFrontendExtensions([]);
const ExtensionContext = createContext<FrontendExtensionRegistry>(EMPTY);

export function ExtensionProvider({
  children,
  extensions = compiledExtensions,
}: {
  children: React.ReactNode;
  extensions?: readonly FrontendExtension[];
}) {
  const registry = useMemo(
    () => registerFrontendExtensions(extensions),
    [extensions],
  );
  const [decisions, setDecisions] = useState<Record<string, Decision> | null>(
    registry.policyActions.length ? null : {},
  );
  const [identityRevision, setIdentityRevision] = useState(0);

  useEffect(() => {
    const refresh = () => {
      setDecisions(null);
      setIdentityRevision((current) => current + 1);
    };
    window.addEventListener("storage", refresh);
    return () => window.removeEventListener("storage", refresh);
  }, []);

  useEffect(() => {
    if (!registry.policyActions.length) return;
    let live = true;
    const query = encodeURIComponent(registry.policyActions.join(","));
    api<CapabilityResponse>(`/api/capabilities?actions=${query}`)
      .then((value) => {
        if (live) setDecisions(value.actions);
      })
      .catch(() => {
        if (live) setDecisions({});
      });
    return () => {
      live = false;
    };
  }, [identityRevision, registry]);

  const visible = useMemo(() => {
    const permitted = (action?: string) =>
      !action || decisions?.[action]?.effect === "permit";
    return Object.freeze({
      ...registry,
      navigation: Object.freeze(
        registry.navigation.filter((item) => permitted(item.policyAction)),
      ),
      dashboardCards: Object.freeze(
        registry.dashboardCards.filter((item) => permitted(item.policyAction)),
      ),
    });
  }, [decisions, registry]);

  return (
    <ExtensionContext.Provider value={visible}>
      {children}
    </ExtensionContext.Provider>
  );
}

export function useFrontendExtensions() {
  return useContext(ExtensionContext);
}
