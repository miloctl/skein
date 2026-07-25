"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

import { API_URL, api, getApiKey, getUser, setApiKey, setUser } from "@/lib/api";
import {
  APPEARANCES,
  COLORWAYS,
  getAppearance,
  getColorway,
  setAppearance,
  setColorway,
} from "@/lib/theme";

function subscribeStorage(cb: () => void) {
  window.addEventListener("storage", cb);
  return () => window.removeEventListener("storage", cb);
}

type WhoAmI = { user: string; strong: boolean; keys_minted: number };

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-card p-4 shadow-card">
      <h2 className="mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
        {title}
      </h2>
      {children}
    </section>
  );
}

function CopyLine({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-center gap-2">
      <code className="flex-1 overflow-x-auto rounded bg-raised px-2 py-1 text-xs">
        {text}
      </code>
      <button
        onClick={() => {
          navigator.clipboard.writeText(text).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          });
        }}
        className="shrink-0 rounded bg-raised px-2 py-1 text-xs hover:bg-line"
      >
        {copied ? "✓ copied" : "copy"}
      </button>
    </div>
  );
}

export default function SettingsPage() {
  const currentUser = useSyncExternalStore(subscribeStorage, getUser, () => "anonymous");
  const [name, setName] = useState("");
  const [keyDraft, setKeyDraft] = useState("");
  const [who, setWho] = useState<WhoAmI | null>(null);
  const [keyStatus, setKeyStatus] = useState<string>("");
  const [interests, setInterests] = useState("");

  const refresh = useCallback(() => {
    api<WhoAmI>("/api/whoami").then(setWho).catch(() => setWho(null));
  }, []);

  useEffect(refresh, [refresh]);

  const [roster, setRoster] = useState<{ name: string; kind: string; active: number }[]>([]);
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
      alert(String(e));
    }
  };

  const saveName = () => {
    setUser(name);
    window.location.reload();
  };

  const testAndSaveKey = async () => {
    const candidate = keyDraft.trim();
    if (!candidate) return;
    if (!candidate.startsWith("sk-strands-")) {
      setKeyStatus("❌ that doesn't look like a Skein key — they start with sk-strands-");
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
        setKeyStatus("❌ that key is invalid or revoked — check for typos, or mint a new one");
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
      setKeyStatus(`❌ ${String(e)}`);
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

  const appearance = useSyncExternalStore(subscribeStorage, getAppearance, () => "system");
  const colorway = useSyncExternalStore(subscribeStorage, getColorway, () => "indigo");

  // the My Day checklist dismissal lives in this browser's localStorage;
  // restoring is just deleting the flag (progress is recomputed server-side)
  const checklistHidden = useSyncExternalStore(
    subscribeStorage,
    () =>
      window.localStorage.getItem(`skein-onboarded:${currentUser}`) === "1",
    () => false,
  );
  const restoreChecklist = () => {
    window.localStorage.removeItem(`skein-onboarded:${currentUser}`);
    window.dispatchEvent(new Event("storage"));
  };

  return (
    <main className="mx-auto w-full max-w-2xl space-y-4 p-6">
      <h1 className="font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">Settings</h1>
      <p className="text-sm text-ink-3">
        Everything you need to set up lives here — nothing requires reading
        the docs first.
      </p>

      <Section title="Appearance">
        <p className="mb-3 text-sm text-ink-3">
          Per-browser, applies immediately. Colorways re-dye the accent
          threads; every one passes contrast checks in both modes.
        </p>
        <div className="mb-4 flex items-center gap-2">
          <span className="w-24 text-sm text-ink-2">Mode</span>
          <div className="flex overflow-hidden rounded-lg border border-line-strong">
            {APPEARANCES.map((a) => (
              <button
                key={a.id}
                onClick={() => setAppearance(a.id)}
                aria-pressed={appearance === a.id}
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
          <span className="w-24 pt-1.5 text-sm text-ink-2">Colorway</span>
          <div className="flex flex-wrap gap-2">
            {COLORWAYS.map((c) => (
              <button
                key={c.id}
                onClick={() => setColorway(c.id)}
                aria-pressed={colorway === c.id}
                className={
                  "flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm transition-colors " +
                  (colorway === c.id
                    ? "border-thread-solid bg-thread/10 font-medium text-ink"
                    : "border-line-strong text-ink-2 hover:bg-raised")
                }
              >
                <span aria-hidden className="flex">
                  <span
                    className="size-3 rounded-full ring-1 ring-line-strong"
                    style={{ background: c.thread }}
                  />
                  <span
                    className="-ml-1 size-3 rounded-full ring-1 ring-line-strong"
                    style={{ background: c.weld }}
                  />
                </span>
                {c.label}
              </button>
            ))}
          </div>
        </div>
      </Section>

      <Section title="1 · Identity">
        <p className="mb-2 text-sm text-ink-3">
          Your name attributes everything you create (tasks, standups,
          captures). It is the trusted-LAN identity — fine for team-visible
          work, not enough for private surfaces (step 2 covers those).
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
            placeholder={currentUser === "anonymous" ? "your name" : "change name"}
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
          The People page (1:1 prep, feedback journal) and admin export need a
          key — a spoofable name isn&apos;t enough for private data. The key
          also powers the CLI and git hooks.
        </p>
        <p className="mb-3 text-sm">
          Status:{" "}
          {strong ? (
            <span className="font-medium text-ok">
              ● strong identity active as {who?.user}
            </span>
          ) : hasBrowserKey ? (
            <span className="font-medium text-danger">
              A key is stored in this browser but it is not working — paste
              a fresh one below
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
                    ? "Can't verify your key status right now (the stored key may be revoked). If you need a fresh one, whoever runs the box mints it:"
                    : `No key has been minted for ${currentUser} yet. Whoever runs the box mints your first one (it prints once — they send it to you privately):`}
                </p>
                <CopyLine text={`python -m app.bootstrap_key ${currentUser}`} />
                <p className="mt-2 text-xs text-ink-3">
                  Docker:{" "}
                  <code>
                    docker compose exec backend python -m app.bootstrap_key {currentUser}
                  </code>
                </p>
              </>
            ) : (
              <p>
                A key exists for {who.user} — paste it below. Lost it? Keys are
                shown only once; ask for a new one to be minted (same command),
                or revoke old ones from the CLI.
              </p>
            )}
          </div>
        )}
        <div className="flex gap-2">
          <input
            value={keyDraft}
            onChange={(e) => setKeyDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && testAndSaveKey()}
            placeholder="sk-strands-…"
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
            <button
              onClick={clearKey}
              className="rounded-lg bg-raised px-3 py-1.5 text-sm text-ink-2 hover:bg-line"
            >
              Remove
            </button>
          )}
        </div>
        {keyStatus && <p className="mt-2 text-sm">{keyStatus}</p>}
        <p className="mt-2 text-xs text-ink-3">
          Stored only in this browser (localStorage). OIDC sign-in replaces
          this flow at deployment.
        </p>
      </Section>

      <Section title="3 · Growth interests (optional)">
        <p className="mb-2 text-sm text-ink-3">
          Self-declared; shown in staffing what-ifs so interesting work finds
          you. Display-only — never scored, never matched automatically.
        </p>
        <div className="flex gap-2">
          <input
            value={interests}
            onChange={(e) => setInterests(e.target.value)}
            placeholder="e.g. RAG evaluation, incident command, design reviews"
            className="flex-1 rounded-lg border border-line-strong bg-transparent px-3 py-1.5 text-sm outline-none focus:border-thread-solid"
          />
          <button
            onClick={async () => {
              try {
                await api("/api/users/growth-interests", {
                  method: "POST",
                  body: JSON.stringify({ interests }),
                });
                setInterests("");
                alert("Saved.");
              } catch (e) {
                alert(String(e));
              }
            }}
            disabled={!interests.trim()}
            className="rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
          >
            Save
          </button>
        </div>
      </Section>

      <Section title="4 · Connect your own AI agent (optional)">
        <p className="mb-2 text-sm text-ink-3">
          Skein is an MCP server — Claude Code or any MCP client can read and
          write the platform natively. New agents start at{" "}
          <b>review</b> authority: every write becomes a proposal in{" "}
          <a href="/review" className="underline">
            /review
          </a>{" "}
          until a human grants more (see{" "}
          <a href="/agents" className="underline">
            /agents
          </a>
          ).
        </p>
        <p className="mb-1 text-xs font-medium text-ink-3">Claude Code registration:</p>
        <CopyLine
          text={`claude mcp add skein -- env STRANDS_MCP_USER=${currentUser === "anonymous" ? "you" : currentUser} <path-to-backend>/.venv/bin/python -m app.mcp_server`}
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
          Subscribe your calendar app to team events + milestone/commitment
          due dates. Use a local calendar client — hosted ones (Google) would
          mirror titles off-LAN.
        </p>
        <CopyLine text={`${API_URL}/api/calendar.ics`} />
        <p className="mt-2 text-xs text-ink-3">
          If the API is token-locked, the operator sets STRANDS_ICS_TOKEN and
          the URL becomes …/api/calendar.ics?token=&lt;that token&gt;.
        </p>
      </Section>

      <Section title="Team roster">
        <p className="mb-2 text-sm text-ink-3">
          Everyone who has picked a name here. Deactivating removes a typo or
          departed teammate from the roster and counts, and revokes their API
          keys — history stays attributed. Requires a working API key (step
          2).
        </p>
        {roster.length === 0 ? (
          <p className="text-sm text-ink-3">Nobody yet.</p>
        ) : (
          <ul className="space-y-1">
            {roster.map((u) => (
              <li
                key={u.name}
                className={
                  "flex items-center justify-between text-sm" +
                  (u.active ? "" : " opacity-60")
                }
              >
                <span>
                  {u.name}
                  {u.kind === "agent" && (
                    <span className="ml-1.5 rounded-full bg-thread/10 px-1.5 py-px font-mono text-[10px] text-thread">
                      agent
                    </span>
                  )}
                  {!u.active && (
                    <span className="ml-1.5 rounded-full bg-raised px-1.5 py-px font-mono text-[10px] text-ink-3">
                      inactive
                    </span>
                  )}
                </span>
                {strong && u.name !== who?.user && (
                  <span className="flex gap-1.5">
                    <button
                      onClick={async () => {
                        const to = prompt(
                          `Rename ${u.name} to (merges history if the name exists):`,
                        );
                        if (!to?.trim()) return;
                        try {
                          await api(`/api/users/${encodeURIComponent(u.name)}/rename`, {
                            method: "POST",
                            body: JSON.stringify({ new_name: to.trim() }),
                          });
                          loadRoster();
                        } catch (e) {
                          alert(String(e));
                        }
                      }}
                      className="rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
                    >
                      rename…
                    </button>
                    {u.active ? (
                      <button
                        onClick={() => {
                          if (
                            confirm(
                              `Deactivate ${u.name}? History stays; the name leaves the roster and their API keys are revoked.`,
                            )
                          )
                            setActive(u.name, false);
                        }}
                        className="rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
                      >
                        deactivate
                      </button>
                    ) : (
                      <button
                        onClick={() => setActive(u.name, true)}
                        className="rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
                      >
                        reactivate
                      </button>
                    )}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Dismissed cards">
        {checklistHidden ? (
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-ink-2">
              The <b>first-week checklist</b> on My Day is hidden in this
              browser. Your progress was never lost — it retires itself for
              good once all six steps are done.
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
            first-week checklist on My Day, this is where you bring it back.
          </p>
        )}
        <p className="mt-2 text-xs text-ink-3">
          Dismissed NOTICE items are just markers — the proposals, blockers,
          and reviews they point to stay visible on{" "}
          <a href="/review" className="underline">
            Review
          </a>{" "}
          and the{" "}
          <a href="/dashboard" className="underline">
            Dashboard
          </a>{" "}
          until acted on.
        </p>
      </Section>
    </main>
  );
}
