"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

import {
  API_URL,
  actionError,
  api,
  backendUnreachable,
  getApiKey,
  getUser,
  isUnreachable,
  loadError,
  setApiKey,
  setUser,
} from "@/lib/api";
import { reportStatus } from "@/lib/status";
import { copyText } from "@/lib/clipboard";
import { Card as Section } from "@/components/card";
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

type WhoAmI = {
  user: string;
  strong: boolean;
  admin: boolean;
  keys_minted: number;
};

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

export default function SettingsPage() {
  const currentUser = useSyncExternalStore(
    subscribeStorage,
    getUser,
    () => "anonymous",
  );
  const [name, setName] = useState("");
  const [keyDraft, setKeyDraft] = useState("");
  const [who, setWho] = useState<WhoAmI | null>(null);
  // null until known: section 4 tells someone what happens when they
  // connect an agent, and the answer INVERTS with the review gate
  const [gateOn, setGateOn] = useState<boolean | null>(null);
  const [whoError, setWhoError] = useState("");
  const [keyStatus, setKeyStatus] = useState<string>("");
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
  // `settled` is the knob that was just written, and ONLY its draft is
  // dropped. Clearing the whole map threw away half-typed values on every
  // other knob in the list, with nothing said — a save on knob A silently
  // reverted the reader's unsaved edit to knob B.
  const loadTunables = useCallback((settled?: string) => {
    api<Tunable[]>("/api/settings/tuning")
      .then((r) => {
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
        setTunables(null);
        // the same helper the long-chat section uses, so a refusal the server
        // answered never reads as an unreachable backend. A non-administrator
        // lands here too, and the server's own 403 sentence already tells
        // them what they need — this must not re-word it (CLAUDE.md)
        setTuneLoadError(loadError(e));
      })
      .finally(() => setTuneLoaded(true));
  }, []);
  // Gated on strong identity: this read is AdminUser, so firing it for
  // every visitor put a 403 in the console on every settings load, and the
  // e2e no-4xx sweep counted it on all six fabric packs. `who` arrives
  // asynchronously, so this runs when identity resolves, not on mount.
  useEffect(() => {
    if (who?.strong) loadTunables();
  }, [loadTunables, who?.strong]);
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
    api<{
      strategy: string;
      override: string;
      default: string;
      choices: string[];
      applies: boolean;
    }>("/api/settings/context-strategy")
      .then((r) => {
        setCtx(r);
        setCtxLoadError("");
      })
      .catch((e) => {
        setCtx(null);
        // a 401 behind SKEIN_API_TOKEN, or a 500 from a locked database, is a
        // server that answered — calling it unreachable sends the reader to
        // check something that is running
        setCtxLoadError(loadError(e)); // routes to backendUnreachable itself
      })
      .finally(() => setCtxLoaded(true));
  }, []);
  useEffect(loadCtx, [loadCtx]);

  useEffect(() => {
    api<{ review_gate: boolean }>("/api/agents/status")
      .then((s) => setGateOn(s.review_gate))
      .catch(() => setGateOn(null)); // unknown stays unknown
  }, []);

  const refresh = useCallback(() => {
    api<WhoAmI>("/api/whoami")
      .then((w) => {
        setWho(w);
        setWhoError("");
      })
      .catch((e) => {
        // the failure carries its own diagnosis: a served 401 IS the revoked
        // verdict, in the server's words; an unreachable backend gets the one
        // wording. The old text blamed the key for both.
        setWho(null);
        setWhoError(loadError(e));
      });
  }, []);

  useEffect(refresh, [refresh]);

  const [roster, setRoster] = useState<
    { name: string; kind: string; active: number }[]
  >([]);
  const loadRoster = useCallback(() => {
    api<{ name: string; kind: string; active: number }[]>("/api/users?all=1")
      .then((u) => setRoster(u.filter((x) => x.name !== "anonymous")))
      .catch(() => {});
  }, []);
  useEffect(loadRoster, [loadRoster]);

  const setActive = async (name: string, active: boolean) => {
    try {
      await api(`/api/users/${encodeURIComponent(name)}/active`, {
        method: "POST",
        body: JSON.stringify({ active }),
      });
      loadRoster();
    } catch (e) {
      reportStatus(actionError(e));
    }
  };

  const [renaming, setRenaming] = useState<string | null>(null);
  const [deactivating, setDeactivating] = useState<string | null>(null);

  const renameUser = async (from: string, to: string) => {
    if (!to.trim()) return;
    try {
      await api(`/api/users/${encodeURIComponent(from)}/rename`, {
        method: "POST",
        body: JSON.stringify({ new_name: to.trim() }),
      });
      setRenaming(null);
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
      setKeyStatus("❌ that is not a Skein key — keys start with sk-skein-");
      return;
    }
    setKeyStatus("testing…");
    try {
      const res = await fetch(`${API_URL}/api/whoami`, {
        headers: { Authorization: `Bearer ${candidate}`, "X-Client": "web" },
      });
      const w = res.ok ? ((await res.json()) as WhoAmI) : null;
      // strong === true is EXACTLY the property being tested — a malformed
      // key falls through to weak identity and would otherwise "succeed"
      if (!w || !w.strong) {
        setKeyStatus(
          "❌ that key is invalid or revoked — check for typos, or mint a new one",
        );
        return;
      }
      setApiKey(candidate);
      setKeyDraft("");
      setKeyStatus(
        `Key works — you are authenticated as ${w.user}.` +
          (w.user !== getUser() && getUser() !== "anonymous"
            ? ` (note: your display name is ${getUser()} — the key's owner wins for private surfaces)`
            : ""),
      );
      refresh();
    } catch (e) {
      setKeyStatus(`❌ ${actionError(e)}`);
    }
  };

  const clearKey = () => {
    setApiKey("");
    setKeyStatus("key removed from this browser");
    refresh();
  };

  // hydration-safe: server snapshot says "no key", client corrects post-hydration
  const hasBrowserKey = useSyncExternalStore(
    subscribeStorage,
    () => Boolean(getApiKey()),
    () => false,
  );
  const strong = who?.strong ?? false;

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
        {strong && u.name !== who?.user && (
          <span className="flex items-center gap-1.5">
            {renaming === u.name ? (
              <span className="flex items-center gap-1">
                <input
                  autoFocus
                  name="rename-user"
                  aria-label={`Rename ${u.name} to`}
                  placeholder="new name — merges if it exists"
                  onKeyDown={(e) => {
                    if (e.key === "Escape") setRenaming(null);
                    if (e.key === "Enter")
                      renameUser(u.name, (e.target as HTMLInputElement).value);
                  }}
                  className="w-44 rounded-lg border border-line-strong bg-transparent px-2 py-0.5 text-xs outline-none focus:border-thread-solid"
                />
                <button
                  onClick={(e) => {
                    const input = e.currentTarget.parentElement?.querySelector(
                      "input",
                    ) as HTMLInputElement | null;
                    if (input) renameUser(u.name, input.value);
                  }}
                  className="rounded bg-thread-solid px-2 py-0.5 text-xs font-medium text-white hover:opacity-90"
                >
                  save
                </button>
                <button
                  onClick={() => setRenaming(null)}
                  className="text-xs text-ink-3 hover:text-ink"
                >
                  cancel
                </button>
              </span>
            ) : deactivating === u.name ? (
              <span className="flex items-center gap-1 text-xs">
                <span className="text-ink-3">
                  history stays, keys revoked —
                </span>
                <button
                  autoFocus
                  aria-label={`Deactivate ${u.name} — history stays, keys revoked`}
                  onClick={() => {
                    setDeactivating(null);
                    setActive(u.name, false);
                  }}
                  className="rounded bg-danger-solid px-2 py-0.5 font-medium text-white hover:opacity-90"
                >
                  deactivate
                </button>
                <button
                  onClick={() => setDeactivating(null)}
                  className="text-ink-3 hover:text-ink"
                >
                  keep
                </button>
              </span>
            ) : (
              <>
                <button
                  onClick={() => setRenaming(u.name)}
                  className="rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
                >
                  rename…
                </button>
                {u.active ? (
                  <button
                    onClick={() => setDeactivating(u.name)}
                    className="rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
                  >
                    deactivate…
                  </button>
                ) : (
                  <button
                    onClick={() => setActive(u.name, true)}
                    className="rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
                  >
                    reactivate
                  </button>
                )}
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

  // the My Day checklist dismissal lives in this browser's localStorage;
  // restoring is just deleting the flag (progress is recomputed server-side)
  const checklistHidden = useSyncExternalStore(
    subscribeStorage,
    () => window.localStorage.getItem(`skein-onboarded:${currentUser}`) === "1",
    () => false,
  );
  const restoreChecklist = () => {
    window.localStorage.removeItem(`skein-onboarded:${currentUser}`);
    window.dispatchEvent(new Event("storage"));
  };

  return (
    <main
      id="content"
      tabIndex={-1}
      className="mx-auto w-full max-w-5xl p-4 sm:p-6 xl:max-w-6xl"
    >
      <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">
        Settings
      </h1>
      <p className="text-sm text-ink-3">
        Everything you need to set up lives here — nothing requires reading the
        docs first.
      </p>

      <Section title="1 · Identity">
        <p className="mb-2 text-sm text-ink-3">
          Your name attributes everything you create (tasks, standups,
          captures). It works on the honor system inside the team network. That
          is fine for team-visible work, but not enough for the private 1:1s
          page or admin export (step 2 covers those).
        </p>
        <p className="mb-2 text-sm">
          Current:{" "}
          <span className="font-medium">
            {currentUser === "anonymous" ? "not set" : currentUser}
          </span>
        </p>
        <div className="flex gap-2">
          <input
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

      <Section title="2 · Personal API key (private surfaces + CLI)">
        <p className="mb-2 text-sm text-ink-3">
          The 1:1s page (private prep, feedback journal) and admin export need a
          key — a spoofable name is not enough for private data. The key also
          powers the CLI and git hooks.
        </p>
        <p className="mb-3 text-sm">
          Status:{" "}
          {strong ? (
            <span className="font-medium text-ok">
              ● strong identity active as {who?.user}
            </span>
          ) : hasBrowserKey ? (
            <span className="font-medium text-danger">
              A key is stored in this browser but it is not working — paste a
              fresh one below
            </span>
          ) : (
            <span className="font-medium text-weld">
              no key in this browser yet
            </span>
          )}
        </p>
        {!strong && (
          <div className="mb-3 rounded-lg bg-raised p-3 text-sm">
            {currentUser === "anonymous" ? (
              <p>
                Pick your name in <b>step 1</b> first — your key is minted for
                that name.
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
                      setKeyStatus(
                        r.already_pending
                          ? "Already asked — the request is still on the team's My Day."
                          : "Asked — the request (with the exact command) is now on the team's My Day.",
                      );
                    } catch (e) {
                      setKeyStatus(`❌ ${actionError(e)}`);
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
                        docker compose exec backend python -m app.bootstrap_key{" "}
                        {currentUser}
                      </code>{" "}
                      — the key prints once.
                    </p>
                  </div>
                </details>
              </>
            ) : (
              <p>
                A key exists for {who.user} — paste it below. A key shows only
                once. If you lost it, ask whoever runs the server to mint a new
                one (same command), or revoke old ones from the CLI.
              </p>
            )}
          </div>
        )}
        <div className="flex gap-2">
          <input
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
            /* "Delete", not "Remove": docs/LEXICON.md reserves delete for
               destruction, and the 401 this screen exists to answer
               (routes/deps.py::INVALID_KEY) sends the reader here to "delete
               the stored key" — a button with a different verb is the reader
               checking whether they are on the right screen. */
            <button
              onClick={clearKey}
              className="rounded-lg bg-raised px-3 py-1.5 text-sm text-ink-2 hover:bg-line"
            >
              Delete
            </button>
          )}
        </div>
        {keyStatus && (
          <p role="status" aria-live="polite" className="mt-2 text-sm">
            {keyStatus}
          </p>
        )}
        <p className="mt-2 text-xs text-ink-3">
          Stored only in this browser (localStorage). OIDC sign-in replaces this
          flow at deployment.
        </p>
      </Section>

      <Section title="3 · Growth interests (optional)">
        <p className="mb-2 text-sm text-ink-3">
          You declare these yourself. They appear in staffing what-ifs so
          interesting work finds you. Display-only — never scored, never matched
          automatically.
        </p>
        <div className="flex gap-2">
          <input
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
                setInterestsSaved(interests.trim() ? "Saved." : "Cleared.");
                setTimeout(() => setInterestsSaved(""), 3000);
              } catch (e) {
                reportStatus(actionError(e));
              } finally {
                setInterestsBusy(false);
              }
            }}
            disabled={interestsBusy || (!interests.trim() && !interestsLoaded)}
            className="rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
          >
            Save
          </button>
        </div>
        <p role="status" aria-live="polite" className="mt-1 text-xs text-ok">
          {interestsSaved}
        </p>
      </Section>

      <Section title="4 · Connect your own AI agent (optional)">
        <p className="mb-2 text-sm text-ink-3">
          Skein is an MCP server — Claude Code or any MCP client can read and
          write the platform natively. New agents start at <b>needs-approval</b>{" "}
          authority.{" "}
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
              The review gate is off in this deployment, so a connected agent
              writes directly: set an entity to <b>not allowed</b> on{" "}
              <a href="/agents" className="underline">
                /agents
              </a>{" "}
              to stop it, or set <code>SKEIN_AGENT_REVIEW=1</code> to hold every
              write for approval.
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
          Team context pack (org-brain for any agent — also an MCP resource):
        </p>
        <CopyLine text={`${API_URL}/api/context-pack`} />
        <p className="mt-2 text-xs text-ink-3">
          Scoped per-engagement packs: append ?engagement=&lt;id&gt;. The CLI
          can also emit it: <code>skein context --write AGENTS.md</code>.
        </p>
      </Section>

      <Section title="5 · Calendar feed (optional)">
        <p className="mb-2 text-sm text-ink-3">
          Subscribe your calendar app to team events + milestone/promise due
          dates. Use a local calendar client — hosted clients (Google) mirror
          titles outside your network.
        </p>
        <CopyLine text={`${API_URL}/api/calendar.ics`} />
        <p className="mt-2 text-xs text-ink-3">
          If the API is token-locked, whoever runs the server sets
          SKEIN_ICS_TOKEN and the URL becomes …/api/calendar.ics?token=&lt;that
          token&gt;.
        </p>
      </Section>

      <Section title="6 · Code forge webhook (optional)">
        <p className="mb-2 text-sm text-ink-3">
          Let your git forge move tasks. A push to <code>task/42-…</code> starts
          task 42. When the pull request merges, the task finishes. Add this URL
          as a repository webhook. Set the content type to JSON.
        </p>
        <CopyLine text={`${API_URL}/api/webhooks/forge`} />
        <p className="mt-2 text-xs text-ink-3">
          Whoever runs the server sets SKEIN_FORGE_WEBHOOK_SECRET. Put the same
          secret in the webhook. If the secret is not set, the endpoint stays
          closed. Skein ignores a branch name that has no task number. A merge
          never closes a delegated task — the sponsor accepts that work.
        </p>
      </Section>

      <Section title="Appearance">
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
                    <span className="block truncate text-[10px] opacity-60">
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
                  onClick={() => setCustomHues(customThread, customWeld)}
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
                      style={{ background: `oklch(0.44 0.13 ${customThread})` }}
                    />
                    <span
                      className="-ml-1.5 size-4 rounded-full ring-1 ring-line-strong"
                      style={{ background: `oklch(0.47 0.09 ${customWeld})` }}
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
                    <span className="w-24 text-sm text-ink-2">{label}</span>
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
                Theme code — copy to share this exact look, paste to apply one
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
                  disabled={!strong}
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
                  A default, never an override — anyone&apos;s personal choice
                  beats it.{" "}
                  {strong
                    ? "Fresh browsers and anonymous visitors start here."
                    : "Needs your API key (step 2 above) and administrator access."}
                </p>
              </div>
            </div>
          </div>
        )}
      </Section>

      <Section title="Long chats (team)">
        <p className="mb-3 text-sm text-ink-3">
          A model holds only so much of a chat. When a chat outgrows
          that, Skein either drops the oldest messages or summarizes them. This
          setting applies to everyone. Only an administrator can change it, with
          a personal API key (step 2).
          {ctx && !ctx.applies && (
            <> No model is connected. This setting is not in use.</>
          )}
        </p>
        {ctxLoaded && !ctx && (
          <p className="text-sm text-ink-3">{ctxLoadError}</p>
        )}
        {ctx && (
          <div className="space-y-2">
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
                    disabled={!strong}
                    checked={ctx.strategy === o.id}
                    onChange={async () => {
                      try {
                        await api("/api/settings/context-strategy", {
                          method: "POST",
                          body: JSON.stringify({ strategy: o.id }),
                        });
                        setCtxStatus(
                          "Saved. Every chat uses it from its next message.",
                        );
                        loadCtx();
                      } catch (e) {
                        // a served refusal (rate cap, revoked key) is not an
                        // unreachable backend, and saying so sends the reader
                        // to check a server that is running
                        setCtxStatus(
                          isUnreachable(e)
                            ? backendUnreachable()
                            : `Not saved. ${actionError(e)}`,
                        );
                      }
                    }}
                  />
                  <span>
                    <span className="block text-sm font-medium text-ink">
                      {o.title}
                    </span>
                    <span className="block text-xs text-ink-3">{o.note}</span>
                  </span>
                </label>
              ))}
            {ctx.override && (
              <button
                disabled={!strong}
                onClick={async () => {
                  try {
                    await api("/api/settings/context-strategy", {
                      method: "POST",
                      body: JSON.stringify({ strategy: "" }),
                    });
                    setCtxStatus(
                      `Cleared. Back to the deployment default (${ctx.default}).`,
                    );
                    loadCtx();
                  } catch (e) {
                    setCtxStatus(
                      isUnreachable(e)
                        ? backendUnreachable()
                        : `Not cleared. ${actionError(e)}`,
                    );
                  }
                }}
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
              {ctxStatus ||
                (strong
                  ? ""
                  : "Needs your API key (step 2 above) and administrator access.")}
            </p>
          </div>
        )}
      </Section>

      <Section title="Deployment limits (team)">
        <p className="mb-3 text-sm text-ink-3">
          These limits set what Skein allows per person, and how long it waits
          on a model. They apply to everyone. Only an administrator can read or
          change them, with a personal API key (step 2). If you clear a limit,
          Skein uses the server default.
        </p>
        {/* role=status on an always-mounted node: the refusal arrives after
            first paint, and a live region inserted with its own text is not
            announced. The final branch is not dead — a load that returns
            nothing with no error must still say why the section is empty. */}
        <p role="status" className="text-sm text-ink-3 empty:hidden">
          {!strong
            ? "Needs your API key (step 2 above) and administrator access."
            : tuneLoaded && !tunables
              ? tuneLoadError || "Needs administrator access."
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
                      <span className="text-xs text-ink-3">{t.unit}</span>
                    </div>
                  </div>
                  <p
                    id={`tune-${t.name}-help`}
                    className="mt-1 text-xs text-ink-3"
                  >
                    {t.detail} Allowed: {t.floor} to {t.ceiling}. Default:{" "}
                    {t.default}.
                    {!t.live &&
                      " This value is read at startup. A change applies after a restart."}
                  </p>
                  {t.ignored && (
                    <p
                      id={`tune-${t.name}-stored`}
                      className="mt-1 text-xs text-danger"
                    >
                      The stored value {t.override} is outside the allowed
                      range, so Skein uses {t.value} {t.unit}. Save a value
                      inside the range to replace it.
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
                          setTuneStatus(`${t.label}: ${parsed} ${t.unit}.`);
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
                              `${t.label}: back to the server default.`,
                            );
                            loadTunables(t.name);
                          } catch (e) {
                            setTuneStatus(`Not saved. ${actionError(e)}`);
                          } finally {
                            setTuneBusy("");
                          }
                        }}
                        className="rounded-lg border border-line-strong px-3 py-1 text-xs text-ink-2 hover:border-line-strong disabled:opacity-40"
                      >
                        Use the default
                      </button>
                    )}
                    {t.override !== null && !t.ignored && (
                      <span className="text-xs text-ink-3">
                        Set here, not the server default.
                      </span>
                    )}
                    {valid && !changed && t.override === null && (
                      <span className="text-xs text-ink-3">
                        At the server default.
                      </span>
                    )}
                    {!valid && (
                      <span
                        id={`tune-${t.name}-err`}
                        className="text-xs text-danger"
                      >
                        Enter a whole number from {t.floor} to {t.ceiling}.
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

      <CrewsCard
        strong={strong}
        me={who?.user ?? ""}
        admin={who?.admin ?? false}
      />

      <Section title="Team roster">
        <p className="mb-2 text-sm text-ink-3">
          Everyone who has picked a name here. Deactivate a name to remove a
          typo or a departed teammate from the roster and the counts. This also
          revokes their API keys — history stays attributed. Requires a working
          API key (step 2) and administrator access.
        </p>
        <h3 className="mb-1 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
          Teammates
        </h3>
        {roster.filter((u) => u.kind !== "agent").length === 0 ? (
          <p className="text-sm text-ink-3">Nobody yet.</p>
        ) : (
          <ul className="space-y-1">
            {rosterRows(roster.filter((u) => u.kind !== "agent"))}
          </ul>
        )}
        {roster.some((u) => u.kind === "agent") && (
          <>
            <h3 className="mt-5 mb-1 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
              Agent identities
            </h3>
            <p className="mb-2 text-xs text-ink-3">
              Created automatically the first time an agent writes — the
              Chief-of-Staff and any bench persona someone has called with{" "}
              <code>/as</code>. Not teammates — they exist so every write stays
              attributed. Deactivate one to take the name out of use — its history stays.
            </p>
            <ul className="space-y-1">
              {rosterRows(roster.filter((u) => u.kind === "agent"))}
            </ul>
          </>
        )}
      </Section>

      <Section title="Dismissed cards">
        {checklistHidden ? (
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-ink-2">
              The <b>first-week checklist</b> on My Day is hidden in this
              browser. Your progress was never lost — it hides itself for good
              once every step is done.
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
            Nothing is dismissed in this browser. If you dismiss the first-week
            checklist on My Day, this is where you bring it back.
          </p>
        )}
        <p className="mt-2 text-xs text-ink-3">
          Dismissed NOTICE items are just markers — the proposals, blockers, and
          reviews they point to stay visible in{" "}
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

      <Section title="Field guide">
        <p className="text-sm text-ink-2">
          Every Skein feature as a card — tied once you use it, with the how-to
          on the rest. Only you can see your guide.{" "}
          <Link href="/guide" className="font-medium underline">
            Open the field guide
          </Link>
        </p>
      </Section>
    </main>
  );
}
