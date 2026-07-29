"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

import {
  ThreadPrimitive,
  MessagePrimitive,
  ComposerPrimitive,
  useComposer,
  useComposerRuntime,
} from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import remarkGfm from "remark-gfm";

import { api } from "@/lib/api";
import {
  findPersona,
  getActivePersona,
  setActivePersona,
  setBench,
  subscribePersona,
  type Persona,
} from "@/lib/persona";

const MarkdownText = () => (
  <MarkdownTextPrimitive
    remarkPlugins={[remarkGfm]}
    className="prose-chat break-words"
  />
);

const UserMessage = () => (
  <MessagePrimitive.Root className="flex justify-end py-2">
    <div className="max-w-[80%] rounded-2xl bg-thread-solid px-4 py-2.5 text-sm text-white">
      <MessagePrimitive.Parts />
    </div>
  </MessagePrimitive.Root>
);

const AssistantMessage = () => (
  <MessagePrimitive.Root className="flex justify-start py-2">
    <div className="max-w-[85%] rounded-2xl bg-raised px-4 py-2.5 text-sm text-ink">
      <MessagePrimitive.Parts components={{ Text: MarkdownText }} />
    </div>
  </MessagePrimitive.Root>
);

const SUGGESTIONS = [
  "Plan a launch for our new onboarding flow",
  "What's on the calendar this week?",
  "What needs my attention today?",
  "/briefing",
];

type SlashCommand = { name: string; args: string; description: string };

// shipped set as a fallback so autocomplete works even if the catalog
// fetch fails; the backend response replaces it and is the source of truth
const FALLBACK_COMMANDS: SlashCommand[] = [
  { name: "help", args: "", description: "List every command" },
  { name: "briefing", args: "", description: "Your My-Day summary" },
  { name: "search", args: "<query>", description: "Full-text search across the workspace" },
  { name: "plan", args: "<playbook> <engagement name>", description: "Instantiate a playbook as a new engagement" },
  { name: "playbooks", args: "", description: "List available playbooks" },
  { name: "remember", args: "<fact>", description: "Save a durable cross-thread memory" },
];

const Composer = () => {
  const text = useComposer((s) => s.text);
  const composer = useComposerRuntime();
  const [commands, setCommands] = useState<SlashCommand[]>(FALLBACK_COMMANDS);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [sel, setSel] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  const activePersona = useSyncExternalStore(
    subscribePersona,
    getActivePersona,
    () => null,
  );

  useEffect(() => {
    api<SlashCommand[]>("/api/chat/commands").then(setCommands).catch(() => {});
    api<Persona[]>("/api/personas")
      .then((list) => {
        setPersonas(list);
        setBench(list);
        // /agents bench cards link to /chat?as=<slug> — enter that
        // persona's session directly (the chip shows the mode)
        const slug = new URLSearchParams(window.location.search).get("as");
        if (slug) {
          const p = list.find((x) => x.slug === slug);
          if (p) setActivePersona(p);
          // consume the param: without this, every thread switch remounts
          // the composer and silently resurrects a dismissed persona
          const url = new URL(window.location.href);
          url.searchParams.delete("as");
          window.history.replaceState(null, "", url);
        }
      })
      .catch(() => {});
  }, []);

  // two popup modes: the command token ("/bri"), and the persona slug
  // right after "/as " — the hard-to-recall half of the invocation
  const cmdToken = /^\/[a-z]*$/i.test(text) ? text.slice(1).toLowerCase() : null;
  const asToken = /^\/as\s+([a-z0-9-]*)$/i.exec(text)?.[1]?.toLowerCase() ?? null;
  const resetKey = asToken !== null ? `as:${asToken}` : cmdToken;
  const [prevToken, setPrevToken] = useState(resetKey);
  if (prevToken !== resetKey) {
    setPrevToken(resetKey);
    setSel(0);
    setDismissed(false);
  }

  const matches: SlashCommand[] =
    asToken !== null
      ? personas
          .filter((p) => p.slug.startsWith(asToken))
          .map((p) => ({
            name: `as ${p.slug}`,
            args: "<message>",
            description: `${p.emoji} ${p.description}`,
          }))
      : cmdToken === null
        ? []
        : commands.filter((c) => c.name.startsWith(cmdToken));
  const open = !dismissed && matches.length > 0;
  const active = matches[Math.min(sel, matches.length - 1)];

  const run = (c: SlashCommand) => {
    if (c.name.startsWith("as ")) {
      const p = findPersona(c.name.slice(3));
      if (p) {
        setActivePersona(p);
        composer.setText("");
      }
      return;
    }
    if (c.args) {
      composer.setText(`/${c.name} `);
    } else {
      composer.setText(`/${c.name}`);
      composer.send();
    }
  };

  const onKeyDownCapture = (e: React.KeyboardEvent) => {
    if (!open) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSel((s) => (s + 1) % matches.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSel((s) => (s - 1 + matches.length) % matches.length);
    } else if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      e.stopPropagation();
      if (active) run(active);
    } else if (e.key === "Tab") {
      e.preventDefault();
      if (active) composer.setText(`/${active.name}${active.args ? " " : ""}`);
    } else if (e.key === "Escape") {
      e.stopPropagation();
      setDismissed(true);
    }
  };

  return (
    <div className="relative" onKeyDownCapture={onKeyDownCapture}>
      {open && (
        <div
          id="cmd-list"
          role="listbox"
          aria-label="Commands"
          className="absolute inset-x-0 bottom-full mb-2 overflow-hidden rounded-xl border border-line bg-card shadow-float"
        >
          <p className="border-b border-line px-3 py-1.5 font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-ink-3">
            Commands — ↵ to run, tab to complete
          </p>
          {matches.map((c, i) => (
            <button
              key={c.name}
              id={`cmd-${i}`}
              role="option"
              aria-selected={i === sel}
              onMouseEnter={() => setSel(i)}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => run(c)}
              className={`flex w-full items-baseline gap-2 px-3 py-2 text-left text-sm ${
                i === sel ? "bg-thread/10" : ""
              }`}
            >
              <code className="shrink-0 font-medium text-thread">/{c.name}</code>
              {c.args && (
                <code className="shrink-0 text-xs text-ink-3">{c.args}</code>
              )}
              <span className="truncate text-xs text-ink-3">{c.description}</span>
              {i === sel && (
                <kbd className="ml-auto shrink-0 rounded border border-line-strong px-1 font-mono text-[10px] text-ink-3">
                  ↵
                </kbd>
              )}
            </button>
          ))}
        </div>
      )}
      {activePersona && (
        <div className="mb-1.5 flex items-center gap-2 text-xs">
          <span className="flex items-center gap-1.5 rounded-full border border-thread-solid/40 bg-thread/10 py-0.5 pl-2 pr-1 font-medium text-thread">
            <span aria-hidden>{activePersona.emoji}</span>
            {activePersona.name}
            <button
              onClick={() => setActivePersona(null)}
              aria-label={`Leave ${activePersona.name} mode`}
              className="flex min-h-6 min-w-6 items-center justify-center rounded-full leading-none hover:bg-thread/20"
            >
              ×
            </button>
          </span>
          <span className="text-ink-3">
            every message goes to this specialist — × returns to the Chief of
            Staff
          </span>
        </div>
      )}
      <ComposerPrimitive.Root className="flex items-end gap-2 rounded-xl border border-line-strong bg-card p-2 shadow-card">
        <ComposerPrimitive.Input
          role="combobox"
          aria-expanded={open}
          aria-controls="cmd-list"
          aria-activedescendant={open ? `cmd-${sel}` : undefined}
          autoFocus
          placeholder={
            activePersona
              ? `Message ${activePersona.name}…`
              : "Message the Chief of Staff… (/help for commands, or just ask)"
          }
          className="max-h-40 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-ink-3"
          rows={1}
        />
        <ComposerPrimitive.Send className="rounded-lg bg-thread-solid px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-40">
          Send
        </ComposerPrimitive.Send>
      </ComposerPrimitive.Root>
    </div>
  );
};

export function Thread() {
  return (
    <ThreadPrimitive.Root className="flex h-full flex-col">
      <ThreadPrimitive.Viewport className="flex flex-1 flex-col overflow-y-auto px-4 pt-4">
        <ThreadPrimitive.Empty>
          <div className="mx-auto flex max-w-lg flex-1 flex-col items-center justify-center pb-24 text-center">
            <div className="loom-idle mb-6 w-40" aria-hidden />
            <p className="font-display text-2xl font-semibold tracking-tight text-ink">
              Skein Chief of Staff
            </p>
            <p className="mt-2 text-sm text-ink-3">
              Track milestones, log questions, record decisions, post standups,
              and plan projects — just ask.
            </p>
            <div className="mt-6 flex flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((s) => (
                <ThreadPrimitive.Suggestion
                  key={s}
                  prompt={s}
                  method="replace"
                  autoSend
                  className="cursor-pointer rounded-full border border-line-strong bg-card px-3 py-1.5 text-xs text-ink-2 transition-colors hover:border-thread-solid hover:text-thread"
                >
                  {s.startsWith("/") ? <code>{s}</code> : s}
                </ThreadPrimitive.Suggestion>
              ))}
            </div>
            <p className="mt-6 text-xs text-ink-3">
              Type <code>/help</code> for commands — <code>/plan</code>,{" "}
              <code>/playbooks</code>, <code>/search</code>,{" "}
              <code>/briefing</code>, <code>/remember</code> — they run
              instantly, no model needed. <code>/personas</code> lists the
              bench of specialists. Anything else goes to the agent.
            </p>
          </div>
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages
          components={{ UserMessage, AssistantMessage }}
        />
        <ThreadPrimitive.ViewportFooter className="sticky bottom-0 mt-auto bg-gradient-to-t from-page via-page to-transparent pb-4 pt-2">
          <Composer />
        </ThreadPrimitive.ViewportFooter>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
}
