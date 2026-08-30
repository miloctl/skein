"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from "react";

import { api } from "@/lib/api";
import { compiledExtensions } from "@/extensions/generated";
import type { FrontendExtension, FrontendExtensionRegistry } from "./contracts";
import { registerFrontendExtensions } from "./registry";

type Decision = { effect: "permit" | "deny" | "review" };
type CapabilityResponse = { actions: Record<string, Decision> };

const EMPTY = registerFrontendExtensions([]);
const ExtensionContext = createContext<FrontendExtensionRegistry>(EMPTY);
let identityEventRevision = 0;
const identitySnapshot = () => {
  if (typeof window === "undefined") return "0:[]";
  try {
    return `${identityEventRevision}:${JSON.stringify(
      ["skein-oidc", "skein-user", "skein-key"].map((key) =>
        window.localStorage.getItem(key),
      ),
    )}`;
  } catch {
    return `${identityEventRevision}:[]`;
  }
};
const subscribeIdentity = (listener: () => void) => {
  const changed = () => {
    identityEventRevision += 1;
    listener();
  };
  window.addEventListener("storage", changed);
  window.addEventListener("skein-identity-change", changed);
  return () => {
    window.removeEventListener("storage", changed);
    window.removeEventListener("skein-identity-change", changed);
  };
};

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
  const currentIdentityRevision = useSyncExternalStore(
    subscribeIdentity,
    identitySnapshot,
    () => "0:[]",
  );
  const [decisionState, setDecisionState] = useState<{
    revision: string;
    actions: Record<string, Decision>;
  } | null>(
    registry.policyActions.length
      ? null
      : { revision: currentIdentityRevision, actions: {} },
  );

  useEffect(() => {
    if (!registry.policyActions.length) return;
    let live = true;
    const query = encodeURIComponent(registry.policyActions.join(","));
    api<CapabilityResponse>(`/api/capabilities?actions=${query}`)
      .then((value) => {
        if (live)
          setDecisionState({
            revision: currentIdentityRevision,
            actions: value.actions,
          });
      })
      .catch(() => {
        if (live)
          setDecisionState({
            revision: currentIdentityRevision,
            actions: {},
          });
      });
    return () => {
      live = false;
    };
  }, [currentIdentityRevision, registry]);

  const decisions =
    decisionState?.revision === currentIdentityRevision
      ? decisionState.actions
      : null;
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
