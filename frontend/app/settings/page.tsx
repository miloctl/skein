"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import {
  API_URL,
  actionError,
  api,
  backendUnreachable,
  errorFromResponse,
  getApiKey,
  getUser,
  isUnreachable,
  loadError,
  setApiKey,
  setUser,
} from "@/lib/api";
import { signedInUser } from "@/lib/auth";
import { reportStatus } from "@/lib/status";
import { timeAgo } from "@/lib/time";
import { copyText } from "@/lib/clipboard";
import { Card as Section } from "@/components/card";
import { AttachedFilesCard } from "@/components/attached-files-card";
import { BackupCard } from "@/components/backup-card";
import { OperationsCard } from "@/components/operations-card";
import { CrewsCard } from "@/components/crews-card";
import {
  APPEARANCES,
  applyThemeCode,
  themeCode,
  COLORWAYS,
  CUSTOM_DEFAULT,
  PACKS,
  getAppearance,
  getColorway,
  getCustomHues,
  getPack,
  setAppearance,
  setColorway,
  setCustomHues,
  setPack,
} from "@/lib/theme";

function subscribeStorage(cb: () => void) {
  window.addEventListener("storage", cb);
  return () => window.removeEventListener("storage", cb);
}

const WRITE_TIMEOUT_MS = 30_000;

function boundedWrite(path: string, init: RequestInit): Promise<unknown> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), WRITE_TIMEOUT_MS);
  const timeout = new Promise<never>((_resolve, reject) => {
    controller.signal.addEventListener("abort", () => {
      reject(new DOMException("The write timed out", "AbortError"));
    });
  });
  // An aborted write can already have reached the server. The caller reports
  // an unknown result and refreshes instead of retrying it automatically.
  return Promise.race([
    api(path, { ...init, signal: controller.signal }),
    timeout,
  ]).finally(() => window.clearTimeout(timer));
}

function isWriteTimeout(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

type WhoAmI = {
  user: string;
  strong: boolean;
  admin: boolean;
  can_administer: boolean;
  keys_minted: number;
};

type ModelSummary = {
  scope: "team_default";
  note: string;
  rows: { id: string; label: string; value: string; source: string }[];
};

function ModelSummaryBlock({ summary }: { summary: ModelSummary }) {
  return (
    <div className="mb-4">
      <h4 className="mb-1 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
        In force
      </h4>
      <p className="mb-2 text-xs text-ink-3">{summary.note}</p>
      <dl className="overflow-hidden rounded-lg border border-line bg-raised/50">
        {summary.rows.map((row) => (
          <div
            key={row.id}
            className="grid gap-1 border-b border-line px-3 py-2 last:border-b-0 sm:grid-cols-[10rem_minmax(0,1fr)] sm:gap-3"
          >
            <dt className="text-xs font-medium text-ink-3">{row.label}</dt>
            <dd className="min-w-0 text-sm text-ink">
              <span className="block [overflow-wrap:anywhere]">
                {row.value}
              </span>
              {row.source && (
                <span className="block text-xs text-ink-3">{row.source}</span>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function CopyLine({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-center gap-2">
      {/* wraps, never scrolls. overflow-x-auto made this a scroll container
          with no way to reach it by keyboard (axe: scrollable-region-focusable),
          and the fix is not a tabindex — the copy button beside it already
          gives a keyboard user the whole string. Wrapping shows it instead of
          merely making it reachable, and adds no tab stop. */}
      <code className="min-w-0 flex-1 [overflow-wrap:anywhere] rounded bg-raised px-2 py-1 text-xs">
        {text}
      </code>
      <button
        onClick={async () => {
          if (await copyText(text)) {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          }
        }}
        aria-live="polite"
        className="shrink-0 rounded bg-raised px-2 py-1 text-xs hover:bg-line"
      >
        {copied ? "✓ copied" : "copy"}
      </button>
    </div>
  );
}

type KeyRow = {
  id: number;
  prefix: string;
  label: string;
  active: number;
  created_at: string;
  last_used_at: string | null;
};

/** The reader's own keys, with revoke. Minting had a button and revoking did
 *  not — a leaked or lost key was a hand-made API call to kill, in the one
 *  place the page said "revoke old ones from the CLI". Strong identity only:
 *  GET /api/keys is a StrongUser route, because under trusted-header a bare
 *  name would hand out anyone's key metadata. */
function MyKeys() {
  const [keys, setKeys] = useState<KeyRow[] | null>(null);
  const [revoking, setRevoking] = useState<number | null>(null);
  const load = useCallback(() => {
    // the array is tested, not assumed: an older backend behind a newer
    // bundle answers with something else, and .map on it unmounts the whole
    // Settings page for a list that is merely additive
    api<KeyRow[]>("/api/keys")
      .then((rows) => setKeys(Array.isArray(rows) ? rows : []))
      .catch(() => setKeys([]));
  }, []);
  useEffect(load, [load]);

  if (!keys || keys.length === 0) return null;
  return (
    <div className="mb-3 rounded-lg bg-raised p-3 text-sm">
      <p className="mb-2 font-medium">Your keys</p>
      <ul className="space-y-1.5 text-xs">
        {keys.map((k) => (
          <li key={k.id} className="flex flex-wrap items-center gap-2">
            <code>{k.prefix}…</code>
            {k.label ? <span className="text-ink-3">{k.label}</span> : null}
            <span className="text-ink-3">
              {k.active
                ? k.last_used_at
                  ? `last used ${timeAgo(k.last_used_at)}`
                  : "never used"
                : "revoked"}
            </span>
            {k.active ? (
              revoking === k.id ? (
                <span className="flex items-center gap-1.5">
                  <span id={`revoke-key-${k.id}-consequence`}>
                    Revoke this key? The CLI and git hooks that use it stop
                    working. This cannot be undone.
                  </span>
                  <button
                    autoFocus
                    aria-describedby={`revoke-key-${k.id}-consequence`}
                    onClick={async () => {
                      try {
                        await api(`/api/keys/${k.id}`, { method: "DELETE" });
                        setRevoking(null);
                        load();
                      } catch (e) {
                        reportStatus(actionError(e));
                      }
                    }}
                    className="rounded bg-danger-solid px-2 py-0.5 font-medium text-white hover:opacity-90"
                  >
                    Revoke key
                  </button>
                  <button
                    onClick={() => {
                      setRevoking(null);
                      setTimeout(
                        () =>
                          document
                            .getElementById(`revoke-key-${k.id}`)
                            ?.focus(),
                        0,
                      );
                    }}
                    className="text-ink-3 hover:text-ink"
                  >
                    Cancel revocation
                  </button>
                </span>
              ) : (
                <button
                  id={`revoke-key-${k.id}`}
                  aria-label={`Revoke the key ${k.prefix}`}
                  onClick={() => setRevoking(k.id)}
                  className="underline text-ink-3 hover:text-ink-2"
                >
                  revoke…
                </button>
              )
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function SettingsPage() {
  const currentUser = useSyncExternalStore(
    subscribeStorage,
    getUser,
    () => "anonymous",
  );
  const [name, setName] = useState("");
  const [keyDraft, setKeyDraft] = useState("");
  const [who, setWho] = useState<WhoAmI | null>(null);
  // null until known: Connect your own AI agent explains what happens, and
  // the answer INVERTS with the review gate
  const [gateOn, setGateOn] = useState<boolean | null>(null);
  const [whoError, setWhoError] = useState("");
  const [keyStatus, setKeyStatus] = useState<string>("");
  const [keyError, setKeyError] = useState(false);
  const [createdKey, setCreatedKey] = useState("");
  const [creatingKey, setCreatingKey] = useState(false);
  const [deletingKey, setDeletingKey] = useState(false);
  const keyDeleteRef = useRef<HTMLButtonElement>(null);
  const keyDeleteSnapshot = useRef("");
  const [interests, setInterests] = useState("");
  const [interestsSaved, setInterestsSaved] = useState("");
  const [interestsLoaded, setInterestsLoaded] = useState(false);
  const [interestsBusy, setInterestsBusy] = useState(false);
  const [ctx, setCtx] = useState<{
    strategy: string;
    override: string;
    default: string;
    choices: string[];
    applies: boolean;
  } | null>(null);
  // separate from ctx === null: before the first fetch resolves nothing has
  // failed yet, and rendering an error during normal loading is a refusal
  // describing something that did not happen
  const [ctxLoaded, setCtxLoaded] = useState(false);
  const [ctxLoadError, setCtxLoadError] = useState("");
  const [ctxStatus, setCtxStatus] = useState("");
  const [ctxBusy, setCtxBusy] = useState(false);
  const [ctxBusyStrategy, setCtxBusyStrategy] = useState<string | null>(null);
  type ModelMenuEntry = {
    id: string;
    label: string;
    detail: string;
    max_tokens: number | null;
    context_tokens: number | null;
    price: [number, number] | null;
  };
  type ModelPick = {
    model: string;
    override: {
      provider: string;
      model_id: string;
      set_by: string;
      updated_at: string;
    } | null;
    ignored: string;
    default: string;
    menu: ModelMenuEntry[];
    menu_error: string;
    applies: boolean;
    provider: string;
    summary?: ModelSummary;
  };
  const [pick, setPick] = useState<ModelPick | null>(null);
  // three states, the ctx rule above: before the first fetch settles nothing
  // has failed, and an error rendered during normal loading describes
  // something that did not happen
  const [pickLoaded, setPickLoaded] = useState(false);
  const [pickLoadError, setPickLoadError] = useState("");
  const [pickStatus, setPickStatus] = useState("");
  const [pickBusy, setPickBusy] = useState(false);
  const [pickBusyModel, setPickBusyModel] = useState<string | null>(null);
  type Tunable = {
    name: string;
    label: string;
    value: number;
    default: number;
    override: number | null;
    floor: number;
    ceiling: number;
    unit: string;
    live: boolean;
    detail: string;
    ignored: boolean;
  };
  const [tunables, setTunables] = useState<Tunable[] | null>(null);
  // three states, not two: before the first fetch settles nothing has failed,
  // and an error rendered during normal loading describes something that did
  // not happen (docs/LEXICON.md T2)
  const [tuneLoaded, setTuneLoaded] = useState(false);
  const [tuneLoadError, setTuneLoadError] = useState("");
  const [tuneStatus, setTuneStatus] = useState("");
  const [tuneBusy, setTuneBusy] = useState("");
  const [tuneDraft, setTuneDraft] = useState<Record<string, string>>({});
  const identityGeneration = useRef(0);
  const adminGeneration = useRef(0);
  const ctxGeneration = useRef(0);
  const modelGeneration = useRef(0);
  const pickWriteRef = useRef<number | null>(null);
  const ctxWriteRef = useRef<number | null>(null);
  // `settled` is the knob that was just written, and ONLY its draft is
  // dropped. Clearing the whole map threw away half-typed values on every
  // other knob in the list, with nothing said — a save on knob A silently
  // reverted the reader's unsaved edit to knob B.
  const loadTunables = useCallback((settled?: string) => {
    const current = adminGeneration.current;
    api<Tunable[]>("/api/settings/tuning")
      .then((r) => {
        if (current !== adminGeneration.current) return;
        setTunables(r);
        setTuneLoadError("");
        if (settled)
          setTuneDraft((d) => {
            const next = { ...d };
            delete next[settled];
            return next;
          });
      })
      .catch((e) => {
        if (current !== adminGeneration.current) return;
        setTunables(null);
        // the same helper the long-chat section uses, so a refusal the server
        // answered never reads as an unreachable backend. A non-administrator
        // lands here too, and the server's own 403 sentence already tells
        // them what they need — this must not re-word it (CLAUDE.md)
        setTuneLoadError(loadError(e));
      })
      .finally(() => {
        if (current === adminGeneration.current) setTuneLoaded(true);
      });
  }, []);
  useEffect(() => {
    // prefill: a write-only field can neither be reviewed nor cleared. If
    // the GET fails, the empty field must NOT be saveable — an empty save
    // clears the stored value, and a blank-from-failure would erase it.
    api<{ interests: string }>("/api/users/growth-interests")
      .then((r) => {
        setInterests(r.interests);
        setInterestsLoaded(true);
      })
      .catch(() => setInterestsLoaded(false));
  }, [currentUser]);

  const loadCtx = useCallback(() => {
    const identity = adminGeneration.current;
    const current = ++ctxGeneration.current;
    return api<{
      strategy: string;
      override: string;
      default: string;
      choices: string[];
      applies: boolean;
    }>("/api/settings/context-strategy")
      .then((r) => {
        if (
          identity !== adminGeneration.current ||
          current !== ctxGeneration.current
        )
          return;
        setCtx(r);
        setCtxLoadError("");
      })
      .catch((e) => {
        if (
          identity !== adminGeneration.current ||
          current !== ctxGeneration.current
        )
          return;
        setCtx(null);
        // a 401 behind SKEIN_API_TOKEN, or a 500 from a locked database, is a
        // server that answered — calling it unreachable sends the reader to
        // check something that is running
        setCtxLoadError(loadError(e)); // routes to backendUnreachable itself
      })
      .finally(() => {
        if (
          identity === adminGeneration.current &&
          current === ctxGeneration.current
        )
          setCtxLoaded(true);
      });
  }, []);

  const loadPick = useCallback(() => {
    const current = ++modelGeneration.current;
    return api<ModelPick>("/api/settings/model")
      .then((r) => {
        if (current !== modelGeneration.current) return;
        setPick(r);
        setPickLoadError("");
      })
      .catch((e) => {
        if (current !== modelGeneration.current) return;
        setPick(null);
        setPickLoadError(loadError(e)); // routes to backendUnreachable itself
      })
      .finally(() => {
        if (current === modelGeneration.current) setPickLoaded(true);
      });
  }, []);

  const writePick = useCallback(
    async (model: string, success: string) => {
      const identity = identityGeneration.current;
      if (pickWriteRef.current !== null) {
        if (pickWriteRef.current !== identity) {
          setPickStatus(
            "A model change is still saving. When it finishes, try again.",
          );
        }
        return;
      }

      pickWriteRef.current = identity;
      setPickBusy(true);
      setPickBusyModel(model);
      setPickStatus("Saving…");
      try {
        await boundedWrite("/api/settings/model", {
          method: "POST",
          body: JSON.stringify({ model }),
        });
        // The identity can change while the POST is open. Do not refresh or
        // announce the old identity's write under the new identity.
        if (identity !== identityGeneration.current) return;
        await loadPick();
        // The identity can also change during the refresh. A late response
        // must not announce success in the next identity's Settings page.
        if (identity === identityGeneration.current) setPickStatus(success);
      } catch (e) {
        // A rejected old-identity request can arrive after a new identity is
        // active. Its refusal does not belong on the new identity's page.
        if (identity !== identityGeneration.current) return;
        if (isWriteTimeout(e)) {
          setPickStatus(
            "The save timed out. The result is unknown. Check the current setting before you try again.",
          );
          void loadPick();
          return;
        }
        setPickStatus(
          isUnreachable(e)
            ? backendUnreachable()
            : `${model ? "Not saved" : "Not cleared"}. ${actionError(e)}`,
        );
      } finally {
        if (pickWriteRef.current === identity) {
          pickWriteRef.current = null;
          setPickBusy(false);
          setPickBusyModel(null);
          // The write can settle after an identity change. The block message
          // "A model change is still saving." must not outlive the write it
          // reports, and the settled write can have changed the team value.
          if (identity !== identityGeneration.current) {
            setPickStatus("");
            void loadPick();
          }
        }
      }
    },
    [loadPick],
  );

  const writeCtx = useCallback(
    async (strategy: string, success: string) => {
      const identity = identityGeneration.current;
      if (ctxWriteRef.current !== null) {
        if (ctxWriteRef.current !== identity) {
          setCtxStatus(
            "A long-chat change is still saving. When it finishes, try again.",
          );
        }
        return;
      }

      ctxWriteRef.current = identity;
      setCtxBusy(true);
      setCtxBusyStrategy(strategy);
      setCtxStatus("Saving…");
      try {
        await boundedWrite("/api/settings/context-strategy", {
          method: "POST",
          body: JSON.stringify({ strategy }),
        });
        // The identity can change while the POST is open. Do not refresh or
        // announce the old identity's write under the new identity.
        if (identity !== identityGeneration.current) return;
        await Promise.all([loadCtx(), loadPick()]);
        // The identity can also change during either refresh. A late response
        // must not announce success in the next identity's Settings page.
        if (identity === identityGeneration.current) setCtxStatus(success);
      } catch (e) {
        // A rejected old-identity request can arrive after a new identity is
        // active. Its refusal does not belong on the new identity's page.
        if (identity !== identityGeneration.current) return;
        if (isWriteTimeout(e)) {
          setCtxStatus(
            "The save timed out. The result is unknown. Check the current setting before you try again.",
          );
          void loadCtx();
          void loadPick();
          return;
        }
        setCtxStatus(
          isUnreachable(e)
            ? backendUnreachable()
            : `${strategy ? "Not saved" : "Not cleared"}. ${actionError(e)}`,
        );
      } finally {
        if (ctxWriteRef.current === identity) {
          ctxWriteRef.current = null;
          setCtxBusy(false);
          setCtxBusyStrategy(null);
          // The write can settle after an identity change. The block message
          // "A long-chat change is still saving." must not outlive the write
          // it reports, and the settled write can have changed the team value.
          if (identity !== identityGeneration.current) {
            setCtxStatus("");
            void loadCtx();
            void loadPick();
          }
        }
      }
    },
    [loadCtx, loadPick],
  );

  // These reads are AdminUser. Losing that capability invalidates their
  // controls and the protected values already returned to this browser.
  useEffect(() => {
    const current = ++adminGeneration.current;
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled || current !== adminGeneration.current) return;
      setTunables(null);
      setCtx(null);
      setTuneLoaded(false);
      setCtxLoaded(false);
      setTuneLoadError("");
      setCtxLoadError("");
      setCtxStatus("");
      setTuneStatus("");
      if (!who?.can_administer) return;
      loadTunables();
      loadCtx();
    });
    return () => {
      cancelled = true;
    };
  }, [loadCtx, loadTunables, who?.can_administer]);

  // The summary is a CurrentUser read. Every signed-in person can inspect the
  // team default, while the controls below still require AdminUser.
  useEffect(() => {
    const current = ++modelGeneration.current;
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled || current !== modelGeneration.current) return;
      setPick(null);
      setPickLoaded(false);
      setPickLoadError("");
      setPickStatus("");
      if (!who?.user || who.user === "anonymous") return;
      loadPick();
    });
    return () => {
      cancelled = true;
    };
  }, [loadPick, who?.user]);

  useEffect(() => {
    api<{ review_gate: boolean }>("/api/agents/status")
      .then((s) => setGateOn(s.review_gate))
      .catch(() => setGateOn(null)); // unknown stays unknown
  }, []);

  const refresh = useCallback(() => {
    const current = ++identityGeneration.current;
    setWho(null);
    setWhoError("");
    api<WhoAmI>("/api/whoami")
      .then((w) => {
        if (current !== identityGeneration.current) return;
        setWho(w);
        setWhoError("");
      })
      .catch((e) => {
        if (current !== identityGeneration.current) return;
        // the failure carries its own diagnosis: a served 401 IS the revoked
        // verdict, in the server's words; an unreachable backend gets the one
        // wording. The old text blamed the key for both.
        setWho(null);
        setWhoError(loadError(e));
      });
  }, []);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) refresh();
    });
    const unsubscribe = subscribeStorage(refresh);
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [refresh]);

  const [roster, setRoster] = useState<
    { name: string; kind: string; active: number }[]
  >([]);
  const loadRoster = useCallback(() => {
    api<{ name: string; kind: string; active: number }[]>("/api/users?all=1")
      .then((u) => setRoster(u.filter((x) => x.name !== "anonymous")))
      .catch(() => {});
  }, []);
  useEffect(loadRoster, [loadRoster]);

  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameConfirmation, setRenameConfirmation] = useState<{
    from: string;
    to: string;
    merge: boolean;
  } | null>(null);
  const [accessConfirmation, setAccessConfirmation] = useState<{
    name: string;
    active: boolean;
  } | null>(null);

  const setActive = async (name: string, active: boolean) => {
    try {
      await api(`/api/users/${encodeURIComponent(name)}/active`, {
        method: "POST",
        body: JSON.stringify({ active }),
      });
      setAccessConfirmation(null);
      loadRoster();
    } catch (e) {
      reportStatus(actionError(e));
    }
  };

  const prepareRename = (from: string, to: string) => {
    const target = to.trim();
    if (!target) return;
    setRenaming(null);
    setRenameConfirmation({
      from,
      to: target,
      merge: roster.some((u) => u.name === target),
    });
  };

  const renameUser = async (from: string, to: string, merge: boolean) => {
    try {
      await api(`/api/users/${encodeURIComponent(from)}/rename`, {
        method: "POST",
        body: JSON.stringify({ new_name: to, merge }),
      });
      setRenameConfirmation(null);
      loadRoster();
    } catch (e) {
      reportStatus(actionError(e));
    }
  };

  const saveName = () => {
    setUser(name);
    window.location.reload();
  };

  const testAndSaveKey = async () => {
    const candidate = keyDraft.trim();
    if (!candidate) return;
    if (!candidate.startsWith("sk-skein-")) {
      setKeyError(true);
      setKeyStatus(
        "This is not a Skein API key. Skein API keys start with sk-skein-.",
      );
      return;
    }
    setKeyError(false);
    setKeyStatus("Checking the key…");
    try {
      const res = await fetch(`${API_URL}/api/whoami`, {
        headers: { Authorization: `Bearer ${candidate}`, "X-Client": "web" },
      });
      if (!res.ok) {
        if (res.status === 401 || res.status === 403) {
          setKeyError(true);
          setKeyStatus(
            "This key did not establish strong identity. Check the key, then try again.",
          );
          return;
        }
        throw await errorFromResponse(res);
      }
      const w = (await res.json()) as WhoAmI;
      // strong === true is EXACTLY the property being tested — a malformed
      // key falls through to weak identity and would otherwise "succeed"
      if (!w.strong) {
        setKeyError(true);
        setKeyStatus(
          "This key did not establish strong identity. Check it, then try again.",
        );
        return;
      }
      const signedInAs = signedInUser();
      setApiKey(candidate);
      setKeyDraft("");
      setKeyError(false);
      setKeyStatus(
        signedInAs
          ? `Key works and is stored for ${w.user}. Deployment sign-in remains active as ${signedInAs}. The stored key takes effect after you sign out.`
          : `Key works. This browser now uses it as ${w.user}.` +
              (w.user !== getUser() && getUser() !== "anonymous"
                ? ` Your display name remains ${getUser()}.`
                : ""),
      );
    } catch (e) {
      setKeyError(true);
      setKeyStatus(actionError(e));
    }
  };

  const clearKey = () => {
    if (
      !keyDeleteSnapshot.current ||
      getApiKey() !== keyDeleteSnapshot.current
    ) {
      keyDeleteSnapshot.current = "";
      setDeletingKey(false);
      setKeyError(true);
      setKeyStatus(
        "The stored key changed after this confirmation. Confirm the deletion again.",
      );
      return;
    }
    keyDeleteSnapshot.current = "";
    setApiKey("");
    setDeletingKey(false);
    setKeyError(false);
    setKeyStatus(
      "The browser no longer stores this key. No server key was revoked.",
    );
  };

  const createPersonalKey = async () => {
    if (creatingKey) return;
    setCreatingKey(true);
    try {
      const result = await api<{ key: string }>("/api/keys", {
        method: "POST",
        body: JSON.stringify({ label: "CLI and git hooks" }),
      });
      setCreatedKey(result.key);
      setWho((current) =>
        current
          ? { ...current, keys_minted: current.keys_minted + 1 }
          : current,
      );
      setKeyError(false);
      setKeyStatus("");
    } catch (e) {
      setKeyError(true);
      setKeyStatus(actionError(e));
    } finally {
      setCreatingKey(false);
    }
  };

  // hydration-safe: server snapshot says "no key", client corrects post-hydration
  const hasBrowserKey = useSyncExternalStore(
    subscribeStorage,
    () => Boolean(getApiKey()),
    () => false,
  );
  const strong = who?.strong ?? false;
  const canAdminister = who?.can_administer ?? false;
  const adminRequirement = strong
    ? "You do not have administrator access. Ask an administrator for access."
    : "This action requires strong identity and administrator access. If deployment sign-in is available, use it. Otherwise, use a personal API key. If the action is still unavailable, ask an administrator for access.";
  const adminAccessMessage =
    whoError || (who === null ? "Checking identity…" : adminRequirement);

  const rosterRows = (list: { name: string; kind: string; active: number }[]) =>
    list.map((u) => (
      <li
        key={u.name}
        className={
          "flex items-center justify-between text-sm" +
          // NOT opacity: it composites every token at 60% after the theme
          // system has resolved them, measuring 2.3:1 to 3.1:1 in every pack
          // including `contrast`. The badge beside the name already says it.
          (u.active ? "" : " text-ink-3")
        }
      >
        <span>
          {u.name}
          {!u.active && (
            <span className="ml-1.5 rounded-full bg-raised px-1.5 py-px font-mono text-[10px] text-ink-3">
              inactive
            </span>
          )}
        </span>
        {canAdminister && u.name !== who?.user && (
          <span className="flex items-center gap-1.5">
            {renameConfirmation?.from === u.name ? (
              <span
                onKeyDown={(e) => {
                  if (e.key !== "Escape") return;
                  setRenameConfirmation(null);
                  setTimeout(
                    () =>
                      document.getElementById(`rename-user-${u.name}`)?.focus(),
                    0,
                  );
                }}
                className="flex max-w-md flex-col items-end gap-1 text-right text-xs"
              >
                <span
                  id={`rename-user-${u.name}-consequence`}
                  className="text-ink-3"
                >
                  {renameConfirmation.merge
                    ? `Merge ${u.name} into ${renameConfirmation.to}? Team-visible attribution and crew memberships will be combined under ${renameConfirmation.to}. Active personal API keys for ${u.name} will stay active under ${renameConfirmation.to}. Existing activity history under each name will stay as written. This merge cannot later be separated.`
                    : `Rename ${u.name} to ${renameConfirmation.to}? Team-visible attribution and crew memberships will move to ${renameConfirmation.to}. Existing activity history will stay under ${u.name}.`}
                </span>
                <span className="flex items-center gap-1">
                  <button
                    autoFocus
                    aria-describedby={`rename-user-${u.name}-consequence`}
                    onClick={() =>
                      renameUser(
                        u.name,
                        renameConfirmation.to,
                        renameConfirmation.merge,
                      )
                    }
                    className="rounded bg-danger-solid px-2 py-0.5 font-medium text-white hover:opacity-90"
                  >
                    {renameConfirmation.merge
                      ? `Merge ${u.name} into ${renameConfirmation.to}`
                      : `Rename ${u.name}`}
                  </button>
                  <button
                    onClick={() => {
                      setRenameConfirmation(null);
                      setTimeout(
                        () =>
                          document
                            .getElementById(`rename-user-${u.name}`)
                            ?.focus(),
                        0,
                      );
                    }}
                    className="text-ink-3 hover:text-ink"
                  >
                    Cancel {renameConfirmation.merge ? "merge" : "rename"}
                  </button>
                </span>
              </span>
            ) : renaming === u.name ? (
              <span className="flex items-center gap-1">
                <input
                  autoFocus
                  name="rename-user"
                  aria-label={`Rename ${u.name} to`}
                  placeholder="new name"
                  onKeyDown={(e) => {
                    if (e.key === "Escape") {
                      setRenaming(null);
                      setTimeout(
                        () =>
                          document
                            .getElementById(`rename-user-${u.name}`)
                            ?.focus(),
                        0,
                      );
                    }
                    if (e.key === "Enter")
                      prepareRename(
                        u.name,
                        (e.target as HTMLInputElement).value,
                      );
                  }}
                  className="w-44 rounded-lg border border-line-strong bg-transparent px-2 py-0.5 text-xs outline-none focus:border-thread-solid"
                />
                <button
                  onClick={(e) => {
                    const input = e.currentTarget.parentElement?.querySelector(
                      "input",
                    ) as HTMLInputElement | null;
                    if (input) prepareRename(u.name, input.value);
                  }}
                  className="rounded bg-thread-solid px-2 py-0.5 text-xs font-medium text-white hover:opacity-90"
                >
                  save
                </button>
                <button
                  onClick={() => {
                    setRenaming(null);
                    setTimeout(
                      () =>
                        document
                          .getElementById(`rename-user-${u.name}`)
                          ?.focus(),
                      0,
                    );
                  }}
                  className="text-xs text-ink-3 hover:text-ink"
                >
                  cancel
                </button>
              </span>
            ) : accessConfirmation?.name === u.name ? (
              <span
                onKeyDown={(e) => {
                  if (e.key !== "Escape") return;
                  setAccessConfirmation(null);
                  setTimeout(
                    () =>
                      document.getElementById(`access-user-${u.name}`)?.focus(),
                    0,
                  );
                }}
                className="flex max-w-md flex-col items-end gap-1 text-right text-xs"
              >
                <span
                  id={`access-user-${u.name}-consequence`}
                  className="text-ink-3"
                >
                  {accessConfirmation.active
                    ? `Reactivate ${u.name}? ${u.name} will regain access. Revoked personal API keys will stay revoked. Create a new key if ${u.name} needs one.`
                    : `Deactivate ${u.name}? ${u.name} will lose access. History will stay. All personal API keys for ${u.name} will be revoked.`}
                </span>
                <span className="flex items-center gap-1">
                  <button
                    autoFocus
                    aria-describedby={`access-user-${u.name}-consequence`}
                    onClick={() => setActive(u.name, accessConfirmation.active)}
                    className="rounded bg-danger-solid px-2 py-0.5 font-medium text-white hover:opacity-90"
                  >
                    {accessConfirmation.active
                      ? `Reactivate ${u.name}`
                      : `Deactivate ${u.name}`}
                  </button>
                  <button
                    onClick={() => {
                      setAccessConfirmation(null);
                      setTimeout(
                        () =>
                          document
                            .getElementById(`access-user-${u.name}`)
                            ?.focus(),
                        0,
                      );
                    }}
                    className="text-ink-3 hover:text-ink"
                  >
                    Cancel access change
                  </button>
                </span>
              </span>
            ) : (
              <>
                <button
                  id={`rename-user-${u.name}`}
                  onClick={() => {
                    setAccessConfirmation(null);
                    setRenaming(u.name);
                  }}
                  className="rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
                >
                  rename…
                </button>
                <button
                  id={`access-user-${u.name}`}
                  onClick={() => {
                    setRenaming(null);
                    setRenameConfirmation(null);
                    setAccessConfirmation({
                      name: u.name,
                      active: !Boolean(u.active),
                    });
                  }}
                  className="rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
                >
                  {u.active ? "deactivate…" : "reactivate…"}
                </button>
              </>
            )}
          </span>
        )}
      </li>
    ));

  const appearance = useSyncExternalStore(
    subscribeStorage,
    getAppearance,
    () => "system",
  );
  const colorway = useSyncExternalStore(
    subscribeStorage,
    getColorway,
    () => "indigo",
  );
  const pack = useSyncExternalStore(subscribeStorage, getPack, () => "loom");
  const [customizeOpen, setCustomizeOpen] = useState(false);
  const [codeDraft, setCodeDraft] = useState("");
  const [codeStatus, setCodeStatus] = useState("");
  const accentOverridden =
    colorway !== (PACKS.find((p) => p.id === pack)?.accent ?? "indigo");
  const customThread = useSyncExternalStore(
    subscribeStorage,
    () => getCustomHues().thread,
    () => CUSTOM_DEFAULT.thread,
  );
  const customWeld = useSyncExternalStore(
    subscribeStorage,
    () => getCustomHues().weld,
    () => CUSTOM_DEFAULT.weld,
  );

  // A bearer can resolve to somebody other than the local name. The same
  // server identity that owns My Day data must own its dismissal flag.
  const checklistUser = who?.user ?? "";
  const checklistHidden = useSyncExternalStore(
    subscribeStorage,
    () =>
      Boolean(
        checklistUser &&
        window.localStorage.getItem(`skein-onboarded:${checklistUser}`) === "1",
      ),
    () => false,
  );
  const restoreChecklist = () => {
    if (!checklistUser) return;
    window.localStorage.removeItem(`skein-onboarded:${checklistUser}`);
    window.dispatchEvent(new Event("storage"));
  };

  return (
    <main
      id="content"
      tabIndex={-1}
      className="mx-auto w-full max-w-5xl p-4 sm:p-6 xl:max-w-6xl"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">
          Settings
        </h1>
        <Link href="/guide" className="text-sm font-medium underline">
          Open the field guide
        </Link>
      </div>
      <p className="mt-1 text-sm text-ink-3">
        Everything you need to set up lives here — nothing requires reading the
        docs first.
      </p>

      <div className="mt-6 grid gap-6 lg:grid-cols-[10rem_minmax(0,1fr)] lg:items-start">
        <nav
          aria-label="Settings sections"
          className="flex flex-wrap gap-2 lg:sticky lg:top-[calc(var(--nav-h)+1rem)] lg:flex-col"
        >
          <a
            href="#settings-you"
            className="rounded-lg px-3 py-2 text-sm text-ink-2 hover:bg-raised hover:text-ink"
          >
            You
          </a>
          <a
            href="#settings-connections"
            className="rounded-lg px-3 py-2 text-sm text-ink-2 hover:bg-raised hover:text-ink"
          >
            Connections
          </a>
          <a
            href="#settings-ai-runtime"
            className="rounded-lg px-3 py-2 text-sm text-ink-2 hover:bg-raised hover:text-ink"
          >
            AI runtime
          </a>
          <a
            href="#settings-team"
            className="rounded-lg px-3 py-2 text-sm text-ink-2 hover:bg-raised hover:text-ink"
          >
            Team
          </a>
        </nav>

        <div className="min-w-0 space-y-10">
          <section id="settings-you" aria-labelledby="settings-you-heading">
            <h2
              id="settings-you-heading"
              className="mb-3 font-display text-lg font-semibold text-ink"
            >
              You
            </h2>
            <div className="space-y-4">
              <Section title="Identity" headingLevel={3}>
                <p className="mb-2 text-sm text-ink-3">
                  Your name attributes everything you create (tasks, standups,
                  captures). It works on the honor system inside the team
                  network. That is fine for team-visible work, but not enough
                  for the private 1:1s page or admin export. Strong identity
                  covers those.
                </p>
                <p className="mb-2 text-sm">
                  Current:{" "}
                  <span className="font-medium">
                    {currentUser === "anonymous" ? "not set" : currentUser}
                  </span>
                </p>
                <div className="flex gap-2">
                  <input
                    name="display-name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && saveName()}
                    aria-label="Your name"
                    placeholder={
                      currentUser === "anonymous" ? "your name" : "change name"
                    }
                    className="flex-1 rounded-lg border border-line-strong bg-transparent px-3 py-1.5 text-sm outline-none focus:border-thread-solid"
                  />
                  <button
                    onClick={saveName}
                    disabled={!name.trim()}
                    className="rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
                  >
                    Save
                  </button>
                </div>
              </Section>

              <Section
                title="Strong identity and personal API key"
                headingLevel={3}
              >
                <p className="mb-2 text-sm text-ink-3">
                  Deployment sign-in or a personal API key opens private web
                  surfaces. The CLI and git hooks require a personal API key.
                  Team administration also requires administrator access.
                </p>
                <p className="mb-3 text-sm">
                  Status:{" "}
                  {who === null ? (
                    <span
                      className={
                        whoError ? "font-medium text-danger" : "text-ink-3"
                      }
                    >
                      {whoError || "Checking strong identity…"}
                    </span>
                  ) : strong ? (
                    <span className="font-medium text-ok">
                      ● strong identity active as {who.user}
                    </span>
                  ) : hasBrowserKey ? (
                    <span className="font-medium text-danger">
                      The stored value did not establish strong identity. Delete
                      it from this browser, then save a valid personal API key.
                    </span>
                  ) : (
                    <span className="font-medium text-weld">
                      Strong identity is not active.
                    </span>
                  )}
                </p>
                {who !== null && !strong && (
                  <div className="mb-3 rounded-lg bg-raised p-3 text-sm">
                    {currentUser === "anonymous" ? (
                      <p>
                        Pick your name under <b>Identity</b> first — your key is
                        minted for that name.
                      </p>
                    ) : who === null || who.keys_minted === 0 ? (
                      <>
                        <p className="mb-2">
                          {who === null
                            ? whoError || "Checking the key status…"
                            : `No key exists for ${currentUser} yet — ask whoever runs the server to mint one and send it to you privately.`}
                        </p>
                        <button
                          onClick={async () => {
                            try {
                              const r = await api<{ already_pending: boolean }>(
                                "/api/keys/request",
                                {
                                  method: "POST",
                                },
                              );
                              setKeyError(false);
                              setKeyStatus(
                                r.already_pending
                                  ? "Already asked — the request is still on the team's My Day."
                                  : "Asked — the request (with the exact command) is now on the team's My Day.",
                              );
                            } catch (e) {
                              setKeyError(true);
                              setKeyStatus(actionError(e));
                            }
                          }}
                          className="mb-2 rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
                        >
                          Request a key
                        </button>
                        <details>
                          <summary className="cursor-pointer text-xs text-ink-3 hover:text-ink-2">
                            I run the server — show me the command
                          </summary>
                          <div className="mt-2">
                            <CopyLine
                              text={`python -m app.bootstrap_key ${currentUser}`}
                            />
                            <p className="mt-2 text-xs text-ink-3">
                              Docker:{" "}
                              <code>
                                docker compose exec backend python -m
                                app.bootstrap_key {currentUser}
                              </code>{" "}
                              — the key prints once.
                            </p>
                          </div>
                        </details>
                      </>
                    ) : (
                      <p>
                        A key exists for {who.user} — paste it below. A key
                        shows only once. If you lost it, ask whoever runs the
                        server to mint a new one (same command), or revoke old
                        ones from the CLI.
                      </p>
                    )}
                  </div>
                )}
                {who !== null &&
                  strong &&
                  who.keys_minted === 0 &&
                  !createdKey && (
                    <div className="mb-3 rounded-lg bg-raised p-3 text-sm">
                      <p className="mb-2">
                        No personal API key exists for {who.user}. Create one
                        for the CLI and git hooks.
                      </p>
                      <button
                        type="button"
                        onClick={createPersonalKey}
                        disabled={creatingKey}
                        className="rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
                      >
                        {creatingKey
                          ? "Creating…"
                          : "Create a personal API key"}
                      </button>
                    </div>
                  )}
                {createdKey && (
                  <div
                    role="status"
                    className="mb-3 rounded-lg border border-weld/40 bg-weld/10 p-3"
                  >
                    <p className="mb-2 text-sm">
                      Copy this key now. Skein will not show it again.
                    </p>
                    <CopyLine text={createdKey} />
                  </div>
                )}
                {/* keyed on createdKey too, so the list re-reads after a mint */}
                {strong ? <MyKeys key={createdKey || "keys"} /> : null}
                <div className="flex gap-2">
                  <input
                    name="personal-api-key"
                    autoComplete="off"
                    value={keyDraft}
                    onChange={(e) => setKeyDraft(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && testAndSaveKey()}
                    aria-label="Personal API key"
                    placeholder="sk-skein-…"
                    type="password"
                    className="flex-1 rounded-lg border border-line-strong bg-transparent px-3 py-1.5 font-mono text-sm outline-none focus:border-thread-solid"
                  />
                  <button
                    onClick={testAndSaveKey}
                    disabled={!keyDraft.trim()}
                    className="rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
                  >
                    Test & save
                  </button>
                  {hasBrowserKey && (
                    <span
                      onKeyDown={(e) => {
                        if (e.key !== "Escape" || !deletingKey) return;
                        keyDeleteSnapshot.current = "";
                        setDeletingKey(false);
                        requestAnimationFrame(() =>
                          keyDeleteRef.current?.focus(),
                        );
                      }}
                      className="flex flex-wrap items-center gap-2"
                    >
                      {deletingKey && (
                        <span
                          id="delete-browser-key-consequence"
                          className="max-w-sm text-xs text-danger"
                        >
                          Delete this key from this browser? This does not
                          revoke a server key.
                        </span>
                      )}
                      <button
                        ref={keyDeleteRef}
                        aria-describedby={
                          deletingKey
                            ? "delete-browser-key-consequence"
                            : undefined
                        }
                        onClick={() => {
                          if (deletingKey) {
                            clearKey();
                            return;
                          }
                          keyDeleteSnapshot.current = getApiKey();
                          setDeletingKey(true);
                        }}
                        className="rounded-lg bg-raised px-3 py-1.5 text-sm text-ink-2 hover:bg-line"
                      >
                        {deletingKey
                          ? "Delete from browser"
                          : "Delete from browser…"}
                      </button>
                      {deletingKey && (
                        <button
                          onClick={() => {
                            keyDeleteSnapshot.current = "";
                            setDeletingKey(false);
                            requestAnimationFrame(() =>
                              keyDeleteRef.current?.focus(),
                            );
                          }}
                          className="rounded px-2 py-1.5 text-sm text-ink-3 hover:text-ink"
                        >
                          Cancel deletion
                        </button>
                      )}
                    </span>
                  )}
                </div>
                {keyStatus && (
                  <p
                    role={keyError ? "alert" : "status"}
                    aria-live="polite"
                    className={
                      "mt-2 text-sm" + (keyError ? " text-danger" : "")
                    }
                  >
                    {keyStatus}
                  </p>
                )}
                <p className="mt-2 text-xs text-ink-3">
                  This browser stores the key in local storage. Deployment
                  sign-in opens private web surfaces, but the CLI and git hooks
                  still use a personal API key.
                </p>
              </Section>

              <Section title="Growth interests (optional)" headingLevel={3}>
                <p className="mb-2 text-sm text-ink-3">
                  You declare these yourself. They appear in staffing what-ifs
                  so interesting work finds you. Display-only — never scored,
                  never matched automatically.
                </p>
                <div className="flex gap-2">
                  <input
                    name="growth-interests"
                    value={interests}
                    onChange={(e) => setInterests(e.target.value)}
                    aria-label="Growth interests"
                    placeholder="for example: RAG evaluation, incident command, design reviews"
                    className="flex-1 rounded-lg border border-line-strong bg-transparent px-3 py-1.5 text-sm outline-none focus:border-thread-solid"
                  />
                  <button
                    onClick={async () => {
                      if (interestsBusy) return;
                      setInterestsBusy(true);
                      try {
                        await api("/api/users/growth-interests", {
                          method: "POST",
                          body: JSON.stringify({ interests }),
                        });
                        setInterestsSaved(
                          interests.trim() ? "Saved." : "Cleared.",
                        );
                        setTimeout(() => setInterestsSaved(""), 3000);
                      } catch (e) {
                        reportStatus(actionError(e));
                      } finally {
                        setInterestsBusy(false);
                      }
                    }}
                    disabled={
                      interestsBusy || (!interests.trim() && !interestsLoaded)
                    }
                    className="rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
                  >
                    Save
                  </button>
                </div>
                <p
                  role="status"
                  aria-live="polite"
                  className="mt-1 text-xs text-ok"
                >
                  {interestsSaved}
                </p>
              </Section>

              <Section title="Appearance" headingLevel={3}>
                <p className="mb-3 text-sm text-ink-3">
                  {currentUser === "anonymous"
                    ? "Saved in this browser — pick your name and it follows you everywhere."
                    : "Saved to your profile — the whole theme, custom colors included, follows you to any browser."}
                </p>
                <div className="mb-4 flex items-center gap-2">
                  <span className="w-24 text-sm text-ink-2">Mode</span>
                  <div className="flex overflow-hidden rounded-lg border border-line-strong">
                    {APPEARANCES.map((a) => (
                      <button
                        key={a.id}
                        onClick={() => setAppearance(a.id)}
                        aria-pressed={appearance === a.id}
                        // the wrapper's overflow-hidden rounds these buttons' corners
                        // and clipped their focus ring with them — globals.css draws
                        // it inside instead
                        data-ring="inset"
                        className={
                          "px-3 py-1.5 text-sm transition-colors " +
                          (appearance === a.id
                            ? "bg-thread-solid font-medium text-white"
                            : "text-ink-2 hover:bg-raised")
                        }
                      >
                        {a.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="flex items-start gap-2">
                  <span className="w-24 pt-1.5 text-sm text-ink-2">Theme</span>
                  <div className="grid flex-1 grid-cols-2 gap-2 sm:grid-cols-4">
                    {PACKS.map((p) => {
                      const cw = COLORWAYS.find((c) => c.id === p.accent)!;
                      const selected = pack === p.id;
                      return (
                        <button
                          key={p.id}
                          onClick={() => {
                            setPack(p.id);
                            setColorway(p.accent);
                          }}
                          aria-pressed={selected}
                          className={
                            "rounded-lg border p-1.5 text-left transition-colors " +
                            (selected
                              ? "border-thread-solid bg-thread/10"
                              : "border-line-strong hover:bg-raised")
                          }
                        >
                          <span
                            aria-hidden
                            data-pack={p.id}
                            className="pack-tile block overflow-hidden rounded-md border border-line px-2 py-1.5"
                          >
                            <span className="block truncate text-[11px] font-medium">
                              Standup at 10:00
                            </span>
                            <span className="block truncate text-[10px] opacity-70">
                              3 tasks · 1 question
                            </span>
                            <span className="mt-1.5 flex gap-1">
                              <span
                                className="h-1.5 w-6 rounded-full"
                                style={{ background: cw.thread }}
                              />
                              <span
                                className="h-1.5 w-3 rounded-full"
                                style={{ background: cw.weld }}
                              />
                            </span>
                          </span>
                          <span className="mt-1.5 flex items-center gap-1 px-0.5 text-sm text-ink">
                            {p.label}
                            {selected && (
                              <span aria-hidden className="text-thread">
                                ✓
                              </span>
                            )}
                          </span>
                          {/* ink-2, not ink-3: the SELECTED card tints its background
                      (bg-thread/10), a surface the ink-3 tuning never covered */}
                          <span className="block px-0.5 text-xs text-ink-2">
                            {p.subtitle}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>
                <button
                  onClick={() => setCustomizeOpen((v) => !v)}
                  aria-expanded={customizeOpen}
                  className="mt-3 flex items-center gap-1.5 text-sm text-ink-2 transition-colors hover:text-ink"
                >
                  <span
                    aria-hidden
                    className={
                      "inline-block transition-transform " +
                      (customizeOpen ? "rotate-90" : "")
                    }
                  >
                    ▸
                  </span>
                  Customize & share
                  {accentOverridden && !customizeOpen && (
                    <span
                      aria-label="custom accent active"
                      className="size-2 rounded-full"
                      style={{ background: "var(--thread)" }}
                    />
                  )}
                </button>
                {customizeOpen && (
                  <div className="mt-2 space-y-3 rounded-lg border border-line bg-raised/50 p-3">
                    <div className="flex items-center gap-2">
                      <span className="w-24 text-sm text-ink-2">Accent</span>
                      <div className="flex flex-wrap items-center gap-2">
                        {COLORWAYS.map((c) => (
                          <button
                            key={c.id}
                            onClick={() => setColorway(c.id)}
                            aria-pressed={colorway === c.id}
                            aria-label={c.label}
                            title={c.label}
                            className={
                              "flex items-center rounded-full border p-1 transition-colors " +
                              (colorway === c.id
                                ? "border-thread-solid bg-thread/10"
                                : "border-line-strong hover:bg-raised")
                            }
                          >
                            <span
                              className="size-4 rounded-full ring-1 ring-line-strong"
                              style={{ background: c.thread }}
                            />
                            <span
                              className="-ml-1.5 size-4 rounded-full ring-1 ring-line-strong"
                              style={{ background: c.weld }}
                            />
                          </button>
                        ))}
                        <button
                          onClick={() =>
                            setCustomHues(customThread, customWeld)
                          }
                          aria-pressed={colorway === "custom"}
                          className={
                            "flex items-center gap-1.5 rounded-full border py-1 pl-1 pr-2.5 text-sm transition-colors " +
                            (colorway === "custom"
                              ? "border-thread-solid bg-thread/10 font-medium text-ink"
                              : "border-line-strong text-ink-2 hover:bg-raised")
                          }
                        >
                          <span aria-hidden className="flex">
                            <span
                              className="size-4 rounded-full ring-1 ring-line-strong"
                              style={{
                                background: `oklch(0.44 0.13 ${customThread})`,
                              }}
                            />
                            <span
                              className="-ml-1.5 size-4 rounded-full ring-1 ring-line-strong"
                              style={{
                                background: `oklch(0.47 0.09 ${customWeld})`,
                              }}
                            />
                          </span>
                          Custom
                        </button>
                      </div>
                    </div>
                    {colorway === "custom" && (
                      <>
                        <p className="text-xs text-ink-3">
                          Pick any hue — every combination stays readable.
                        </p>
                        {(
                          [
                            [
                              "Accent",
                              customThread,
                              (v: number) => setCustomHues(v, customWeld),
                            ],
                            [
                              "Second accent",
                              customWeld,
                              (v: number) => setCustomHues(customThread, v),
                            ],
                          ] as const
                        ).map(([label, value, onChange]) => (
                          <div key={label} className="flex items-center gap-3">
                            <span className="w-24 text-sm text-ink-2">
                              {label}
                            </span>
                            <input
                              type="range"
                              min={0}
                              max={359}
                              value={value}
                              onChange={(e) => onChange(Number(e.target.value))}
                              aria-label={`${label} hue`}
                              aria-valuetext={`${value} degrees`}
                              className="hue-track flex-1"
                            />
                            <span
                              aria-hidden
                              className="size-4 shrink-0 rounded-full ring-1 ring-line-strong"
                              style={{
                                background: `light-dark(oklch(0.44 0.13 ${value}), oklch(0.8 0.09 ${value}))`,
                              }}
                            />
                          </div>
                        ))}
                      </>
                    )}
                    <div className="mt-4 border-t border-line pt-3">
                      <p className="mb-1.5 text-xs font-medium text-ink-2">
                        Theme code — copy to share this exact look, paste to
                        apply one
                      </p>
                      <CopyLine text={themeCode()} />
                      <div className="mt-1.5 flex gap-2">
                        <input
                          value={codeDraft}
                          onChange={(e) => setCodeDraft(e.target.value)}
                          aria-label="Paste a theme code"
                          placeholder='paste a theme code {"pack":…}'
                          className="flex-1 rounded-lg border border-line-strong bg-transparent px-2 py-1 font-mono text-xs outline-none focus:border-thread-solid"
                        />
                        <button
                          disabled={!codeDraft.trim()}
                          onClick={() => {
                            const ok = applyThemeCode(codeDraft.trim());
                            setCodeStatus(
                              ok
                                ? "Applied."
                                : "That is not a valid theme code — check the paste.",
                            );
                            if (ok) setCodeDraft("");
                          }}
                          className="rounded-lg bg-thread-solid px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-40"
                        >
                          Apply
                        </button>
                      </div>
                      <p
                        role="status"
                        aria-live="polite"
                        className="mt-1 min-h-4 text-xs text-ink-3"
                      >
                        {codeStatus}
                      </p>
                      <div className="mt-3 border-t border-line pt-3">
                        <button
                          disabled={!canAdminister}
                          onClick={async () => {
                            try {
                              await api("/api/users/theme/default", {
                                method: "POST",
                                body: JSON.stringify({ theme: themeCode() }),
                              });
                              setCodeStatus(
                                "Saved as the team default — fresh browsers and anonymous visitors start here.",
                              );
                            } catch (e) {
                              setCodeStatus(actionError(e));
                            }
                          }}
                          className="rounded-lg bg-weld/15 px-3 py-1 text-xs font-medium text-weld hover:bg-weld/25 disabled:opacity-40"
                        >
                          Make this the team default
                        </button>
                        <p className="mt-1 text-xs text-ink-3">
                          A default, never an override — anyone&apos;s personal
                          choice beats it.{" "}
                          {canAdminister
                            ? "Fresh browsers and anonymous visitors start here."
                            : adminAccessMessage}
                        </p>
                      </div>
                    </div>
                  </div>
                )}
              </Section>

              <AttachedFilesCard headingLevel={3} />

              <Section title="Dismissed cards" headingLevel={3}>
                {/* While whoami is unresolved the flag is unreadable — claiming
            "nothing is dismissed" there is a false sentence during every
            load, and a lie whenever the request failed outright. */}
                {!checklistUser ? (
                  <p className="text-sm text-ink-3">
                    Skein resolves your identity first. Dismissed cards show
                    after that.
                  </p>
                ) : checklistHidden ? (
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm text-ink-2">
                      The <b>first-week checklist</b> on My Day is hidden in
                      this browser. Your progress was never lost — it hides
                      itself for good once every step is done.
                    </p>
                    <button
                      onClick={restoreChecklist}
                      className="shrink-0 rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
                    >
                      Bring it back
                    </button>
                  </div>
                ) : (
                  <p className="text-sm text-ink-3">
                    Nothing is dismissed in this browser. If you dismiss the
                    first-week checklist on My Day, this is where you bring it
                    back.
                  </p>
                )}
                <p className="mt-2 text-xs text-ink-3">
                  Dismissed NOTICE items are just markers — the proposals,
                  blockers, and reviews they point to stay visible in{" "}
                  <a href="/review" className="underline">
                    Inbox → Approvals
                  </a>{" "}
                  and{" "}
                  <a href="/dashboard" className="underline">
                    Work → Browse
                  </a>{" "}
                  until acted on.
                </p>
              </Section>
            </div>
          </section>

          <section
            id="settings-connections"
            aria-labelledby="settings-connections-heading"
          >
            <h2
              id="settings-connections-heading"
              className="mb-3 font-display text-lg font-semibold text-ink"
            >
              Connections
            </h2>
            <div className="space-y-4">
              <Section
                title="Connect your own AI agent (optional)"
                headingLevel={3}
              >
                <p className="mb-2 text-sm text-ink-3">
                  Skein is an MCP server — Claude Code or any MCP client can
                  read and write the platform natively. New agents start at{" "}
                  <b>needs-approval</b> authority.{" "}
                  {gateOn === null ? (
                    <>
                      What that holds back depends on the review gate —{" "}
                      <a href="/agents" className="underline">
                        /agents
                      </a>{" "}
                      states the rule in force.
                    </>
                  ) : gateOn ? (
                    <>
                      Every write becomes a proposal in{" "}
                      <a href="/review" className="underline">
                        Inbox → Approvals
                      </a>{" "}
                      until a human grants more (see{" "}
                      <a href="/agents" className="underline">
                        /agents
                      </a>
                      ).
                    </>
                  ) : (
                    <>
                      The review gate is off in this deployment, so current
                      grants write directly. Expired elevated grants still wait
                      for review. Set an entity to <b>not allowed</b> on{" "}
                      <a href="/agents" className="underline">
                        /agents
                      </a>{" "}
                      to stop it, or set <code>SKEIN_AGENT_REVIEW=1</code> to
                      hold every write for approval.
                    </>
                  )}
                </p>
                <p className="mb-1 text-xs font-medium text-ink-3">
                  Claude Code registration:
                </p>
                <CopyLine
                  text={`claude mcp add skein -- env SKEIN_MCP_USER=${currentUser === "anonymous" ? "you" : currentUser} <path-to-backend>/.venv/bin/python -m app.mcp_server`}
                />
                <p className="mb-1 mt-3 text-xs font-medium text-ink-3">
                  Team context pack (org-brain for any agent — also an MCP
                  resource):
                </p>
                <CopyLine text={`${API_URL}/api/context-pack`} />
                <p className="mt-2 text-xs text-ink-3">
                  Scoped per-engagement packs: append ?engagement=&lt;id&gt;.
                  The CLI can also emit it:{" "}
                  <code>skein context --write AGENTS.md</code>.
                </p>
              </Section>

              <Section title="Calendar feed (optional)" headingLevel={3}>
                <p className="mb-2 text-sm text-ink-3">
                  The feed includes team event titles and descriptions, plus
                  milestone and promise dates. A calendar app can copy this data
                  outside the network.
                </p>
                <CopyLine text={`${API_URL}/api/calendar.ics`} />
                <p className="mt-2 text-xs text-ink-3">
                  If the API is token-locked, whoever runs the server sets
                  SKEIN_ICS_TOKEN and the URL becomes
                  …/api/calendar.ics?token=&lt;that token&gt;. The tokenized URL
                  grants feed access only to a client that can reach the Skein
                  API. Treat the full URL as a credential. Do not share it.
                </p>
              </Section>

              <Section title="Code forge webhook (optional)" headingLevel={3}>
                <p className="mb-2 text-sm text-ink-3">
                  Let your git forge move tasks. A push to{" "}
                  <code>task/42-…</code> starts task 42. When the pull request
                  merges, the task finishes. Add this URL as a repository
                  webhook. Set the content type to JSON.
                </p>
                <CopyLine text={`${API_URL}/api/webhooks/forge`} />
                <p className="mt-2 text-xs text-ink-3">
                  Whoever runs the server sets SKEIN_FORGE_WEBHOOK_SECRET. Put
                  the same secret in the webhook. If the secret is not set, the
                  endpoint stays closed. Skein ignores a branch name that has no
                  task number. A merge never closes a delegated task — the
                  sponsor accepts that work.
                </p>
              </Section>
            </div>
          </section>

          <section
            id="settings-ai-runtime"
            aria-labelledby="settings-ai-runtime-heading"
          >
            <h2
              id="settings-ai-runtime-heading"
              className="mb-3 font-display text-lg font-semibold text-ink"
            >
              AI runtime
            </h2>
            <div className="space-y-4">
              <Section title="Model (team)" headingLevel={3}>
                <p className="mb-3 text-sm text-ink-3">
                  This section shows the team-default model configuration.
                  Whoever runs the server curates the menu. An administrator
                  with strong identity selects the team model here. The
                  selection applies to the next message from each person.
                  {pick && pick.provider === "mock" && (
                    <> No model is connected. This setting is not in use.</>
                  )}
                </p>
                {pick?.summary && <ModelSummaryBlock summary={pick.summary} />}
                {!canAdminister && (
                  <p className="text-sm text-ink-3">{adminAccessMessage}</p>
                )}
                {pickLoaded && !pick && (
                  <p className="text-sm text-ink-3">{pickLoadError}</p>
                )}
                {canAdminister && pick && pick.menu_error && (
                  <p className="text-sm text-weld">{pick.menu_error}</p>
                )}
                {canAdminister &&
                  pick &&
                  !pick.menu_error &&
                  pick.menu.length === 0 &&
                  pick.provider !== "mock" && (
                    <p className="text-sm text-ink-3">
                      The deployment has no model menu. Whoever runs the server
                      can set SKEIN_MODELS to add one.
                    </p>
                  )}
                {/* pick.override alone is enough to render: a stored pick must stay
            visible and clearable even when the menu is gone or faulted. A clear
            removes that final control, so pickStatus keeps its receipt mounted. */}
                {canAdminister &&
                  pick &&
                  (pick.applies || pick.override || pickStatus) && (
                    <div className="space-y-2" aria-busy={pickBusy}>
                      {pick.ignored && pick.override && (
                        // the stored pick and the reason it is not honored, both named:
                        // hiding either tells the administrator the deployment is
                        // configured one way while it runs another
                        <p className="text-xs text-weld">
                          The stored pick ({pick.override.model_id}) is not in
                          use. {pick.ignored}
                        </p>
                      )}
                      {pick.applies &&
                        pick.menu.map((m) => (
                          <label
                            key={m.id}
                            className={
                              "flex cursor-pointer items-start gap-3 rounded-xl border px-3 py-2.5 " +
                              (pick.model === m.id
                                ? "border-thread-solid bg-thread-solid/5"
                                : "border-line hover:border-line-strong")
                            }
                          >
                            <input
                              type="radio"
                              name="model-pick"
                              className="mt-1"
                              disabled={
                                !canAdminister ||
                                (pickBusy && pickBusyModel !== m.id)
                              }
                              checked={pick.model === m.id}
                              onChange={() =>
                                void writePick(
                                  m.id,
                                  "Saved. Every chat uses it from its next message.",
                                )
                              }
                            />
                            <span>
                              <span className="block text-sm font-medium text-ink">
                                {m.label}
                              </span>
                              {m.detail && (
                                <span className="block text-xs text-ink-3">
                                  {m.detail}
                                </span>
                              )}
                              {(m.price || m.context_tokens) && (
                                <span className="block text-xs text-ink-3">
                                  {[
                                    m.price
                                      ? `$${m.price[0]} in / $${m.price[1]} out per million tokens`
                                      : "",
                                    m.context_tokens
                                      ? `${m.context_tokens.toLocaleString("en-US")}-token context`
                                      : "",
                                  ]
                                    .filter(Boolean)
                                    .join(" · ")}
                                </span>
                              )}
                            </span>
                          </label>
                        ))}
                      {pick.override && !pick.ignored && (
                        <p className="text-xs text-ink-3">
                          Set by {pick.override.set_by} on{" "}
                          {pick.override.updated_at.slice(0, 10)}. Overrides the
                          deployment default ({pick.default}).
                        </p>
                      )}
                      {/* no radio is checked when the model in force is outside the
                menu — without this line the section renders choices under
                "the model every chat runs on" while naming that model
                nowhere */}
                      {pick.applies &&
                        pick.model &&
                        !pick.menu.some((m) => m.id === pick.model) && (
                          <p className="text-xs text-ink-3">
                            The deployment default ({pick.default}) is in force.
                            It is not in the menu.
                          </p>
                        )}
                      {pick.override && (
                        <button
                          disabled={
                            !canAdminister || (pickBusy && pickBusyModel !== "")
                          }
                          onClick={() =>
                            void writePick(
                              "",
                              `Cleared. Back to the deployment default (${pick.default}).`,
                            )
                          }
                          className="rounded-lg bg-weld/15 px-3 py-1 text-xs font-medium text-weld hover:bg-weld/25 disabled:opacity-40"
                        >
                          Use the deployment default ({pick.default})
                        </button>
                      )}
                      <p
                        role="status"
                        aria-live="polite"
                        className="min-h-4 text-xs text-ink-3"
                      >
                        {pickStatus ||
                          (canAdminister ? "" : adminAccessMessage)}
                      </p>
                    </div>
                  )}
              </Section>

              <Section title="Long chats (team)" headingLevel={3}>
                <p className="mb-3 text-sm text-ink-3">
                  A model holds only so much of a chat. When a chat outgrows
                  that, Skein either drops the oldest messages or summarizes
                  them. This setting applies to everyone. Only an administrator
                  with strong identity can change it.
                  {ctx && !ctx.applies && (
                    <> No model is connected. This setting is not in use.</>
                  )}
                </p>
                {!canAdminister && (
                  <p className="text-sm text-ink-3">{adminAccessMessage}</p>
                )}
                {ctxLoaded && !ctx && canAdminister && (
                  <p className="text-sm text-ink-3">{ctxLoadError}</p>
                )}
                {ctx && (
                  <div className="space-y-2" aria-busy={ctxBusy}>
                    {[
                      {
                        id: "sliding",
                        title: "Drop the oldest messages",
                        note: "Free and instant. What falls out of the window is gone.",
                      },
                      {
                        id: "summarize",
                        title: "Summarize the oldest messages",
                        note: "Keeps the gist of a long chat. Costs one extra model call each time it runs.",
                      },
                    ]
                      .filter((o) => ctx.choices.includes(o.id))
                      .map((o) => (
                        <label
                          key={o.id}
                          className={
                            "flex cursor-pointer items-start gap-3 rounded-xl border px-3 py-2.5 " +
                            (ctx.strategy === o.id
                              ? "border-thread-solid bg-thread-solid/5"
                              : "border-line hover:border-line-strong")
                          }
                        >
                          <input
                            type="radio"
                            name="context-strategy"
                            className="mt-1"
                            disabled={
                              !canAdminister ||
                              (ctxBusy && ctxBusyStrategy !== o.id)
                            }
                            checked={ctx.strategy === o.id}
                            onChange={() =>
                              void writeCtx(
                                o.id,
                                "Saved. Every chat uses it from its next message.",
                              )
                            }
                          />
                          <span>
                            <span className="block text-sm font-medium text-ink">
                              {o.title}
                            </span>
                            <span className="block text-xs text-ink-3">
                              {o.note}
                            </span>
                          </span>
                        </label>
                      ))}
                    {ctx.override && (
                      <button
                        disabled={
                          !canAdminister || (ctxBusy && ctxBusyStrategy !== "")
                        }
                        onClick={() =>
                          void writeCtx(
                            "",
                            `Cleared. Back to the deployment default (${ctx.default}).`,
                          )
                        }
                        className="rounded-lg bg-weld/15 px-3 py-1 text-xs font-medium text-weld hover:bg-weld/25 disabled:opacity-40"
                      >
                        Use the deployment default ({ctx.default})
                      </button>
                    )}
                    <p
                      role="status"
                      aria-live="polite"
                      className="min-h-4 text-xs text-ink-3"
                    >
                      {ctxStatus || (canAdminister ? "" : adminAccessMessage)}
                    </p>
                  </div>
                )}
              </Section>

              <Section title="Deployment limits (team)" headingLevel={3}>
                <p className="mb-3 text-sm text-ink-3">
                  These limits set what Skein allows per person and how long it
                  waits for a model. They apply to everyone. Only an
                  administrator with strong identity can read or change them. If
                  you reset a limit, Skein uses the deployment default.
                </p>
                {/* role=status on an always-mounted node: the refusal arrives after
            first paint, and a live region inserted with its own text is not
            announced. The final branch is not dead — a load that returns
            nothing with no error must still say why the section is empty. */}
                <p role="status" className="text-sm text-ink-3 empty:hidden">
                  {!canAdminister
                    ? adminAccessMessage
                    : tuneLoaded && !tunables
                      ? tuneLoadError ||
                        "The deployment limits did not load. Reload the page."
                      : ""}
                </p>
                {tunables && (
                  <div className="space-y-3">
                    {tunables.map((t) => {
                      const draft = tuneDraft[t.name] ?? String(t.value);
                      const parsed = Number(draft);
                      // a draft is only submittable when it is a whole number inside
                      // the bounds AND different from what is in force. The server
                      // range-checks it again — this is not the guard, it is the
                      // reason the reader is not made to press a button that fails.
                      const valid =
                        draft.trim() !== "" &&
                        Number.isInteger(parsed) &&
                        parsed >= t.floor &&
                        parsed <= t.ceiling;
                      const changed = valid && parsed !== t.value;
                      return (
                        <div
                          key={t.name}
                          className="rounded-xl border border-line px-3 py-2.5"
                        >
                          <div className="flex flex-wrap items-center gap-2">
                            <label
                              htmlFor={`tune-${t.name}`}
                              className="text-sm font-medium text-ink"
                            >
                              {t.label}
                            </label>
                            <div className="flex items-center gap-2">
                              <input
                                id={`tune-${t.name}`}
                                type="number"
                                inputMode="numeric"
                                min={t.floor}
                                max={t.ceiling}
                                value={draft}
                                // every sentence that qualifies this number, named
                                // here: the bounds, the out-of-range notice, and the
                                // validation line all render BELOW two buttons, so a
                                // screen reader never reaches them from the input.
                                // aria-invalid carries the state itself — the range
                                // of a number input is not reliably announced.
                                aria-describedby={
                                  `tune-${t.name}-help` +
                                  (t.ignored ? ` tune-${t.name}-stored` : "") +
                                  (valid ? "" : ` tune-${t.name}-err`)
                                }
                                aria-invalid={!valid || undefined}
                                onChange={(e) =>
                                  setTuneDraft((d) => ({
                                    ...d,
                                    [t.name]: e.target.value,
                                  }))
                                }
                                className="w-24 rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm text-ink"
                              />
                              <span className="text-xs text-ink-3">
                                {t.unit}
                              </span>
                            </div>
                          </div>
                          <p
                            id={`tune-${t.name}-help`}
                            className="mt-1 text-xs text-ink-3"
                          >
                            {t.detail} Allowed: {t.floor} to {t.ceiling}.
                            Default: {t.default}.
                            {!t.live &&
                              " This value is read at startup. A change applies after a restart."}
                          </p>
                          {t.ignored && (
                            <p
                              id={`tune-${t.name}-stored`}
                              className="mt-1 text-xs text-danger"
                            >
                              The stored value {t.override} is outside the
                              allowed range, so Skein uses {t.value} {t.unit}.
                              Save a value inside the range to replace it.
                            </p>
                          )}
                          <div className="mt-2 flex flex-wrap items-center gap-2">
                            <button
                              type="button"
                              // busy, not only unchanged: `changed` stays true for the
                              // whole flight (t.value updates after the reload), so a
                              // double press sent two writes — and each one appends to
                              // the activity ledger, which is never pruned
                              disabled={!changed || tuneBusy === t.name}
                              onClick={async () => {
                                setTuneBusy(t.name);
                                try {
                                  await api("/api/settings/tuning", {
                                    method: "POST",
                                    body: JSON.stringify({
                                      name: t.name,
                                      value: parsed,
                                    }),
                                  });
                                  setTuneStatus(
                                    `${t.label}: ${parsed} ${t.unit}.`,
                                  );
                                  loadTunables(t.name);
                                } catch (e) {
                                  // "Not saved." first, matching the long-chat panel:
                                  // the server's own sentence alone reads as a neutral
                                  // remark beside a button the reader just pressed
                                  setTuneStatus(`Not saved. ${actionError(e)}`);
                                } finally {
                                  setTuneBusy("");
                                }
                              }}
                              className="rounded-lg bg-weld/15 px-3 py-1 text-xs font-medium text-weld hover:bg-weld/25 disabled:opacity-40"
                            >
                              Save
                            </button>
                            {t.override !== null && (
                              <button
                                type="button"
                                disabled={tuneBusy === t.name}
                                onClick={async () => {
                                  setTuneBusy(t.name);
                                  try {
                                    await api("/api/settings/tuning", {
                                      method: "POST",
                                      body: JSON.stringify({
                                        name: t.name,
                                        value: null,
                                      }),
                                    });
                                    setTuneStatus(
                                      `${t.label}: the deployment default is now active.`,
                                    );
                                    loadTunables(t.name);
                                  } catch (e) {
                                    setTuneStatus(
                                      `Not saved. ${actionError(e)}`,
                                    );
                                  } finally {
                                    setTuneBusy("");
                                  }
                                }}
                                className="rounded-lg border border-line-strong px-3 py-1 text-xs text-ink-2 hover:border-line-strong disabled:opacity-40"
                              >
                                Use deployment default
                              </button>
                            )}
                            {t.override !== null && !t.ignored && (
                              <span className="text-xs text-ink-3">
                                Set here, not the deployment default.
                              </span>
                            )}
                            {valid && !changed && t.override === null && (
                              <span className="text-xs text-ink-3">
                                At the deployment default.
                              </span>
                            )}
                            {!valid && (
                              <span
                                id={`tune-${t.name}-err`}
                                className="text-xs text-danger"
                              >
                                Enter a whole number from {t.floor} to{" "}
                                {t.ceiling}.
                              </span>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
                {/* Outside the {tunables} branch and never conditional on its own
            text: a live region inserted in the same paint as its content is
            not announced, and this is the ONLY feedback for a save — the
            long-chat panel above holds the same shape. */}
                <p
                  role="status"
                  aria-live="polite"
                  className="mt-3 min-h-4 text-sm text-ink-2"
                >
                  {tuneStatus}
                </p>
              </Section>
            </div>
          </section>

          <section id="settings-team" aria-labelledby="settings-team-heading">
            <h2
              id="settings-team-heading"
              className="mb-3 font-display text-lg font-semibold text-ink"
            >
              Team
            </h2>
            <div className="space-y-4">
              <OperationsCard headingLevel={3} />

              <BackupCard
                canAdminister={canAdminister}
                accessMessage={adminAccessMessage}
                headingLevel={3}
              />

              <CrewsCard
                strong={strong}
                me={who?.user ?? ""}
                admin={who?.admin ?? false}
                headingLevel={3}
              />

              <Section title="Team roster" headingLevel={3}>
                <p className="mb-2 text-sm text-ink-3">
                  Everyone who has picked a name here. Deactivation blocks
                  access and revokes personal API keys. Existing history stays
                  attributed. Roster changes require strong identity and
                  administrator access.
                </p>
                <h4 className="mb-1 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
                  Teammates
                </h4>
                {roster.filter((u) => u.kind !== "agent").length === 0 ? (
                  <p className="text-sm text-ink-3">Nobody yet.</p>
                ) : (
                  <ul className="space-y-1">
                    {rosterRows(roster.filter((u) => u.kind !== "agent"))}
                  </ul>
                )}
                {roster.some((u) => u.kind === "agent") && (
                  <>
                    <h4 className="mt-5 mb-1 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
                      Agent identities
                    </h4>
                    <p className="mb-2 text-xs text-ink-3">
                      Created automatically the first time an agent writes — the
                      Chief-of-Staff and any bench persona someone has called
                      with <code>/as</code>. Not teammates — they exist so every
                      write stays attributed. Deactivate one to take the name
                      out of use — its history stays.
                    </p>
                    <ul className="space-y-1">
                      {rosterRows(roster.filter((u) => u.kind === "agent"))}
                    </ul>
                  </>
                )}
              </Section>
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}
