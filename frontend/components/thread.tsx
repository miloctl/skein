"use client";

import { useEffect, useState } from "react";

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
  const [sel, setSel] = useState(0);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    api<SlashCommand[]>("/api/chat/commands").then(setCommands).catch(() => {});
  }, []);

  // the popup tracks the command token: "/" plus letters, before any space
  const token = /^\/[a-z]*$/i.test(text) ? text.slice(1).toLowerCase() : null;
  const [prevToken, setPrevToken] = useState(token);
  if (prevToken !== token) {
    setPrevToken(token);
    setSel(0);
    setDismissed(false);
  }

  const matches =
    token === null ? [] : commands.filter((c) => c.name.startsWith(token));
  const open = !dismissed && matches.length > 0;
  const active = matches[Math.min(sel, matches.length - 1)];

  const run = (c: SlashCommand) => {
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
          role="listbox"
          aria-label="Commands"
          className="absolute inset-x-0 bottom-full mb-2 overflow-hidden rounded-xl border border-line bg-card shadow-card"
        >
          <p className="border-b border-line px-3 py-1.5 font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-ink-3">
            Commands — ↵ to run, tab to complete
          </p>
          {matches.map((c, i) => (
            <button
              key={c.name}
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
      <ComposerPrimitive.Root className="flex items-end gap-2 rounded-xl border border-line-strong bg-card p-2 shadow-card">
        <ComposerPrimitive.Input
          autoFocus
          placeholder="Message the Chief of Staff… (/help for commands, or just ask)"
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
              instantly, no model needed. Anything else goes to the agent.
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
