"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

import { API_URL, api, getApiKey, getUser, setApiKey, setUser } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
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
  const currentUser = useSyncExternalStore(subscribeStorage, getUser, () => "anonymous");
  const [name, setName] = useState("");
  const [keyDraft, setKeyDraft] = useState("");
  const [who, setWho] = useState<WhoAmI | null>(null);
  const [keyStatus, setKeyStatus] = useState<string>("");
  const [interests, setInterests] = useState("");
  const [interestsSaved, setInterestsSaved] = useState("");
  const [interestsLoaded, setInterestsLoaded] = useState(false);
  const [interestsBusy, setInterestsBusy] = useState(false);
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

  const rosterRows = (list: { name: string; kind: string; active: number }[]) =>
    list.map((u) => (
      <li
        key={u.name}
        className={
          "flex items-center justify-between text-sm" + (u.active ? "" : " opacity-60")
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
                    const input = (e.currentTarget.parentElement?.querySelector(
                      "input",
                    ) as HTMLInputElement | null);
                    if (input) renameUser(u.name, input.value);
                  }}
                  className="rounded bg-thread-solid px-2 py-0.5 text-xs font-medium text-white hover:opacity-90"
                >
                  save
                </button>
                <button onClick={() => setRenaming(null)} className="text-xs text-ink-3 hover:text-ink">
                  cancel
                </button>
              </span>
            ) : deactivating === u.name ? (
              <span className="flex items-center gap-1 text-xs">
                <span className="text-ink-3">history stays, keys revoked —</span>
                <button
                  autoFocus
                  aria-label={`Deactivate ${u.name} — history stays, keys revoked`}
                  onClick={() => {
                    setDeactivating(null);
                    setActive(u.name, false);
                  }}
                  className="rounded bg-danger px-2 py-0.5 font-medium text-white hover:opacity-90"
                >
                  deactivate
                </button>
                <button onClick={() => setDeactivating(null)} className="text-ink-3 hover:text-ink">
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

  const appearance = useSyncExternalStore(subscribeStorage, getAppearance, () => "system");
  const colorway = useSyncExternalStore(subscribeStorage, getColorway, () => "indigo");
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
    () =>
      window.localStorage.getItem(`skein-onboarded:${currentUser}`) === "1",
    () => false,
  );
  const restoreChecklist = () => {
    window.localStorage.removeItem(`skein-onboarded:${currentUser}`);
    window.dispatchEvent(new Event("storage"));
  };

  return (
    <main className="mx-auto w-full max-w-3xl space-y-4 p-6">
      <h1 className="font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">Settings</h1>
      <p className="text-sm text-ink-3">
        Everything you need to set up lives here — nothing requires reading
        the docs first.
      </p>

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
                  <span className="block px-0.5 text-xs text-ink-3">{p.subtitle}</span>
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
            className={"inline-block transition-transform " + (customizeOpen ? "rotate-90" : "")}
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
                    ["Accent", customThread, (v: number) => setCustomHues(v, customWeld)],
                    ["Second accent", customWeld, (v: number) => setCustomHues(customThread, v)],
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
                      ok ? "Applied." : "That doesn't look like a theme code — check the paste.",
                    );
                    if (ok) setCodeDraft("");
                  }}
                  className="rounded-lg bg-thread-solid px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-40"
                >
                  Apply
                </button>
              </div>
              <p role="status" aria-live="polite" className="mt-1 min-h-4 text-xs text-ink-3">
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
                        setCodeStatus(String(e));
                      }
                    }}
                    className="rounded-lg bg-weld/15 px-3 py-1 text-xs font-medium text-weld hover:bg-weld/25 disabled:opacity-40"
                  >
                    Make this the team default
                  </button>
                  <p className="mt-1 text-xs text-ink-3">
                    A default, never an override — anyone&apos;s personal
                    choice beats it.{" "}
                    {strong
                      ? "Fresh browsers and anonymous visitors start here."
                      : "Needs your API key — step 2 below."}
                  </p>
                </div>
            </div>
          </div>
        )}
      </Section>

      <Section title="1 · Identity">
        <p className="mb-2 text-sm text-ink-3">
          Your name attributes everything you create (tasks, standups,
          captures). It works on the honor system inside the team network —
          fine for team-visible work, not enough for the private 1:1s page
          or admin export (step 2 covers those).
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
          The 1:1s page (private prep, feedback journal) and admin export need a
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
                    ? "Can't verify your key status right now (the stored key may be revoked). Ask whoever runs the server for a fresh one."
                    : `No key exists for ${currentUser} yet — ask whoever runs the server to mint one and send it to you privately.`}
                </p>
                <button
                  onClick={async () => {
                    try {
                      const r = await api<{ already_pending: boolean }>("/api/keys/request", {
                        method: "POST",
                      });
                      setKeyStatus(
                        r.already_pending
                          ? "Already asked — the request is still on the team's My Day."
                          : "Asked — the request (with the exact command) is now on the team's My Day.",
                      );
                    } catch (e) {
                      setKeyStatus(`❌ ${String(e)}`);
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
                    <CopyLine text={`python -m app.bootstrap_key ${currentUser}`} />
                    <p className="mt-2 text-xs text-ink-3">
                      Docker:{" "}
                      <code>
                        docker compose exec backend python -m app.bootstrap_key {currentUser}
                      </code>{" "}
                      — the key prints once.
                    </p>
                  </div>
                </details>
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
            aria-label="Personal API key"
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
        {keyStatus && (
          <p role="status" aria-live="polite" className="mt-2 text-sm">
            {keyStatus}
          </p>
        )}
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
            aria-label="Growth interests"
            placeholder="e.g. RAG evaluation, incident command, design reviews"
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
                alert(String(e));
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
          write the platform natively. New agents start at{" "}
          <b>needs-approval</b> authority: every write becomes a proposal in{" "}
          <a href="/review" className="underline">
            Inbox → Approvals
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
        <h3 className="mb-1 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
          Teammates
        </h3>
        {roster.filter((u) => u.kind !== "agent").length === 0 ? (
          <p className="text-sm text-ink-3">Nobody yet.</p>
        ) : (
          <ul className="space-y-1">{rosterRows(roster.filter((u) => u.kind !== "agent"))}</ul>
        )}
        {roster.some((u) => u.kind === "agent") && (
          <>
            <h3 className="mt-5 mb-1 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
              Agent identities
            </h3>
            <p className="mb-2 text-xs text-ink-3">
              Created automatically the first time an agent writes — the
              Chief-of-Staff and any bench persona someone has called with{" "}
              <code>/as</code>. Not teammates; they exist so every write stays
              attributed. Deactivating one retires the name but keeps its
              history.
            </p>
            <ul className="space-y-1">{rosterRows(roster.filter((u) => u.kind === "agent"))}</ul>
          </>
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
          and reviews they point to stay visible in{" "}
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
    </main>
  );
}
