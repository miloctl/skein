"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

import { API_URL, api, getApiKey, getUser, setApiKey, setUser } from "@/lib/api";

function subscribeStorage(cb: () => void) {
  window.addEventListener("storage", cb);
  return () => window.removeEventListener("storage", cb);
}

type WhoAmI = { user: string; strong: boolean; keys_minted: number };

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500">
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
      <code className="flex-1 overflow-x-auto rounded bg-zinc-100 px-2 py-1 text-xs dark:bg-zinc-800">
        {text}
      </code>
      <button
        onClick={() => {
          navigator.clipboard.writeText(text).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          });
        }}
        className="shrink-0 rounded bg-zinc-200 px-2 py-1 text-xs hover:bg-zinc-300 dark:bg-zinc-700"
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
        `✅ key works — you are authenticated as ${w.user}` +
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

  const hasBrowserKey = typeof window !== "undefined" && Boolean(getApiKey());
  const strong = who?.strong ?? false;

  return (
    <main className="mx-auto w-full max-w-2xl space-y-4 p-6">
      <h1 className="text-xl font-bold">Settings</h1>
      <p className="text-sm text-zinc-500">
        Everything you need to set up lives here — nothing requires reading
        the docs first.
      </p>

      <Section title="1 · Identity">
        <p className="mb-2 text-sm text-zinc-500">
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
            className="flex-1 rounded-lg border border-zinc-300 bg-transparent px-3 py-1.5 text-sm outline-none dark:border-zinc-700"
          />
          <button
            onClick={saveName}
            disabled={!name.trim()}
            className="rounded-lg bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900"
          >
            Save
          </button>
        </div>
      </Section>

      <Section title="2 · Personal API key (private surfaces + CLI)">
        <p className="mb-2 text-sm text-zinc-500">
          The People page (1:1 prep, feedback journal) and admin export need a
          key — a spoofable name isn&apos;t enough for private data. The key
          also powers the CLI and git hooks.
        </p>
        <p className="mb-3 text-sm">
          Status:{" "}
          {strong ? (
            <span className="font-medium text-emerald-600">
              ✅ strong identity active as {who?.user}
            </span>
          ) : hasBrowserKey ? (
            <span className="font-medium text-red-600">
              ⚠ a key is stored in this browser but it is not working — paste
              a fresh one below
            </span>
          ) : (
            <span className="font-medium text-amber-600">
              no key in this browser yet
            </span>
          )}
        </p>
        {!strong && (
          <div className="mb-3 rounded-lg bg-zinc-50 p-3 text-sm dark:bg-zinc-800/50">
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
                <p className="mt-2 text-xs text-zinc-500">
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
            className="flex-1 rounded-lg border border-zinc-300 bg-transparent px-3 py-1.5 font-mono text-sm outline-none dark:border-zinc-700"
          />
          <button
            onClick={testAndSaveKey}
            disabled={!keyDraft.trim()}
            className="rounded-lg bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900"
          >
            Test & save
          </button>
          {hasBrowserKey && (
            <button
              onClick={clearKey}
              className="rounded-lg bg-zinc-100 px-3 py-1.5 text-sm text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800"
            >
              Remove
            </button>
          )}
        </div>
        {keyStatus && <p className="mt-2 text-sm">{keyStatus}</p>}
        <p className="mt-2 text-xs text-zinc-400">
          Stored only in this browser (localStorage). OIDC sign-in replaces
          this flow at deployment.
        </p>
      </Section>

      <Section title="3 · Growth interests (optional)">
        <p className="mb-2 text-sm text-zinc-500">
          Self-declared; shown in staffing what-ifs so interesting work finds
          you. Display-only — never scored, never matched automatically.
        </p>
        <div className="flex gap-2">
          <input
            value={interests}
            onChange={(e) => setInterests(e.target.value)}
            placeholder="e.g. RAG evaluation, incident command, design reviews"
            className="flex-1 rounded-lg border border-zinc-300 bg-transparent px-3 py-1.5 text-sm outline-none dark:border-zinc-700"
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
            className="rounded-lg bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900"
          >
            Save
          </button>
        </div>
      </Section>

      <Section title="4 · Connect your own AI agent (optional)">
        <p className="mb-2 text-sm text-zinc-500">
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
        <p className="mb-1 text-xs font-medium text-zinc-500">Claude Code registration:</p>
        <CopyLine
          text={`claude mcp add skein -- env STRANDS_MCP_USER=${currentUser === "anonymous" ? "you" : currentUser} <path-to-backend>/.venv/bin/python -m app.mcp_server`}
        />
        <p className="mb-1 mt-3 text-xs font-medium text-zinc-500">
          Team context pack (org-brain for any agent — also an MCP resource):
        </p>
        <CopyLine text={`${API_URL}/api/context-pack`} />
        <p className="mt-2 text-xs text-zinc-400">
          Scoped per-engagement packs: append ?engagement=&lt;id&gt;. The CLI
          can also emit it: <code>skein context --write AGENTS.md</code>.
        </p>
      </Section>

      <Section title="5 · Calendar feed (optional)">
        <p className="mb-2 text-sm text-zinc-500">
          Subscribe your calendar app to team events + milestone/commitment
          due dates. Use a local calendar client — hosted ones (Google) would
          mirror titles off-LAN.
        </p>
        <CopyLine text={`${API_URL}/api/calendar.ics`} />
        <p className="mt-2 text-xs text-zinc-400">
          If the API is token-locked, the operator sets STRANDS_ICS_TOKEN and
          the URL becomes …/api/calendar.ics?token=&lt;that token&gt;.
        </p>
      </Section>
    </main>
  );
}
