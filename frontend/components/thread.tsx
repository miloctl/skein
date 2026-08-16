"use client";

import {
  useEffect,
  useState,
  useSyncExternalStore,
  type ComponentPropsWithoutRef,
} from "react";

import {
  ThreadPrimitive,
  MessagePrimitive,
  ComposerPrimitive,
  useComposer,
  useComposerRuntime,
  unstable_useComposerInputHistory,
} from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import remarkGfm from "remark-gfm";

import { api } from "@/lib/api";
import { argQuery, mentionQuery, type ArgItem } from "@/lib/slash";
import { reportStatus } from "@/lib/status";
import {
  findPersona,
  getActivePersona,
  setActivePersona,
  setBench,
  subscribePersona,
  type Persona,
} from "@/lib/persona";

/** An assistant message is untrusted text. An attached document, a fetched
 *  page, or a teammate's note can carry an instruction, and the model repeats
 *  what it read. A rendered `![](https://host/?d=...)` fetches that URL the
 *  moment the line paints, so whatever the agent just read leaves in the query
 *  string with no click and no tool call — tools/_gate.py governs writes and
 *  never sees a read leaving this way. The reference renders as inert text
 *  instead of an <img>. next.config.ts pins img-src as the backstop for a
 *  renderer that regresses. */
const InertImage = ({ src, alt }: ComponentPropsWithoutRef<"img">) => (
  <span className="text-ink-3">
    {alt?.trim() ? `${alt.trim()} ` : ""}
    {`(image: ${typeof src === "string" ? src : ""})`}
  </span>
);

/** The href comes from model output, so it opens with no handle back: without
 *  rel, the opened page reads window.opener and can navigate this tab to a
 *  page that imitates it. react-markdown's defaultUrlTransform already drops a
 *  javascript: href before this renders. The underline is the only thing that
 *  marks a link here — prose-chat gives `a` no color of its own. */
const SafeLink = ({ href, children }: ComponentPropsWithoutRef<"a">) => (
  <a href={href} target="_blank" rel="noopener noreferrer" className="underline">
    {children}
  </a>
);

// hoisted: a components object built inline remounts every node on each token
// of a streaming message
const MARKDOWN_COMPONENTS = { img: InertImage, a: SafeLink };

// exported for __tests__/chat-markdown-inert.test.tsx, which renders it with
// the primitive shimmed: the props ARE the containment
export const MarkdownText = () => (
  <MarkdownTextPrimitive
    remarkPlugins={[remarkGfm]}
    className="prose-chat break-words"
    components={MARKDOWN_COMPONENTS}
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

const COMPOSE_LIMIT = 500;

const SUGGESTIONS = [
  "Plan a launch for our new onboarding flow",
  "What's on the calendar this week?",
  "What needs my attention today?",
  "/briefing",
];

type SlashCommand = {
  name: string;
  args: string;
  description: string;
  // set only on @mention rows: the roster name to splice at the @token, and
  // the section it is listed under. A pick with `mention` never rewrites the
  // whole composer the way a command pick does — the @ sits mid-sentence.
  mention?: string;
  group?: string;
};

// shipped set as a fallback so autocomplete works even if the catalog
// fetch fails; the backend response replaces it and is the source of truth
const FALLBACK_COMMANDS: SlashCommand[] = [
  { name: "help", args: "", description: "List every command" },
  { name: "briefing", args: "", description: "Your My Day summary" },
  {
    name: "search",
    args: "<query>",
    description: "Full-text search across the workspace",
  },
  {
    name: "plan",
    args: "<playbook> <engagement name>",
    description: "Instantiate a playbook as a new engagement",
  },
  { name: "playbooks", args: "", description: "List available playbooks" },
  {
    name: "remember",
    args: "<fact>",
    description: "Save a durable cross-thread memory",
  },
  {
    name: "personas",
    args: "",
    description: "List the bench of invokable specialist personas",
  },
  {
    name: "flocks",
    args: "",
    description:
      "List the flocks — groups of personas you can call at one time",
  },
  {
    name: "as",
    args: "<persona> <message>",
    description: "Ask a bench persona instead of the Chief of Staff",
  },
  {
    name: "flock",
    args: "<flock> <message>",
    description: "Ask a flock of personas at one time",
  },
];

// Promise-cached for the life of the page: these catalogs change only on a
// server restart, and RuntimeProvider's key={threadId} remounts the Composer
// on every thread switch — uncached, each switch costs two requests. The
// authConfig() shape (lib/auth.ts): a failed read is not cached, so the
// next mount retries.
let commandsCache: Promise<SlashCommand[]> | null = null;
function chatCommands(): Promise<SlashCommand[]> {
  if (!commandsCache) {
    const attempt = api<SlashCommand[]>("/api/chat/commands").catch((e) => {
      if (commandsCache === attempt) commandsCache = null;
      throw e;
    });
    commandsCache = attempt;
  }
  return commandsCache;
}

let personasCache: Promise<Persona[]> | null = null;
function personaList(): Promise<Persona[]> {
  if (!personasCache) {
    const attempt = api<Persona[]>("/api/personas").catch((e) => {
      if (personasCache === attempt) personasCache = null;
      throw e;
    });
    personasCache = attempt;
  }
  return personasCache;
}

// The roster the @ picker lists under People. Agent rows share this table
// (/as and /flock mint them), so the picker filters by kind rather than
// showing every persona anyone has ever invoked — personaList() above is the
// honest source for specialists.
type Person = { name: string; kind: string };

// the charset services/mentions.py::_MENTION can tokenize. A roster name is
// free-form — ensure_user only strips and truncates — so "O'Brien" and "José"
// are real names the picker used to offer: the apostrophe and the accent end
// the token, the backend matches nobody, and the turn guard cannot report a
// miss it never saw either. Filtering on spaces alone missed both.
const MENTIONABLE = /^[a-z0-9][a-z0-9._-]*$/i;

// Cached like the catalogs above, but the roster is NOT restart-stable: a
// person who joins mid-session is not offered until the page reloads. The
// picker missing a brand-new name costs one manual @type; a fetch per thread
// switch costs a request on every switch, in every open tab.
// Whether a mid-sentence @slug can reach the bench at all: the orchestrator
// consults only on a real provider, and the mock has no tool loop — offering
// a specialist there promises an answer the keyless path cannot give. The
// leading-@ rows never read this: /as is deterministic on every provider.
// A failed fetch leaves it unresolved and the rows hidden, which fails
// toward the path that always works.
let statusCache: Promise<{ provider: string }> | null = null;
function agentStatus(): Promise<{ provider: string }> {
  if (!statusCache) {
    const attempt = api<{ provider: string }>("/api/agents/status").catch((e) => {
      if (statusCache === attempt) statusCache = null;
      throw e;
    });
    statusCache = attempt;
  }
  return statusCache;
}

let peopleCache: Promise<Person[]> | null = null;
function peopleList(): Promise<Person[]> {
  if (!peopleCache) {
    const attempt = api<Person[]>("/api/users").catch((e) => {
      if (peopleCache === attempt) peopleCache = null;
      throw e;
    });
    peopleCache = attempt;
  }
  return peopleCache;
}

// Only the fields the argument popup reads. /api/flocks also carries the
// resolved member cards and the synthesis flag, which the composer never
// shows — a flock is picked by slug here, not inspected.
type Flock = { slug: string; description: string; emoji: string };

let flocksCache: Promise<Flock[]> | null = null;
function flockList(): Promise<Flock[]> {
  if (!flocksCache) {
    const attempt = api<Flock[]>("/api/flocks").catch((e) => {
      if (flocksCache === attempt) flocksCache = null;
      throw e;
    });
    flocksCache = attempt;
  }
  return flocksCache;
}

const Composer = () => {
  const text = useComposer((s) => s.text);
  const composer = useComposerRuntime();
  const [commands, setCommands] = useState<SlashCommand[]>(FALLBACK_COMMANDS);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [flocks, setFlocks] = useState<Flock[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [consultReady, setConsultReady] = useState(false);
  const [sel, setSel] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  // set while the person is walking input history, and NOT reset by resetKey
  // below: a recalled slash command changes the token, so resetting it there
  // would reopen the popup on the very entry that recall just produced
  const [recalling, setRecalling] = useState(false);
  // ArrowUp recalls this thread's own sent messages, newest first. History is
  // derived from the thread runtime's message list, which is what isolates one
  // chat from another's; runtime-provider.tsx's /messages import is what makes
  // a reloaded thread recall anything at all. The library's own popover guard
  // is inert here (this popup is hand-rolled, not Unstable_TriggerPopoverRoot).
  // Two things then hold recall off: the hook's own "recall only from an empty
  // draft" rule, and preventDefault below. Only preventDefault covers a walk
  // already in progress, which is why `recalling` keeps the popup shut.
  const inputHistory = unstable_useComposerInputHistory();
  const activePersona = useSyncExternalStore(
    subscribePersona,
    getActivePersona,
    () => null,
  );

  useEffect(() => {
    const url = new URL(window.location.href);
    const prefill = url.searchParams.get("compose");
    if (prefill) {
      if (prefill.length <= COMPOSE_LIMIT) composer.setText(prefill);
      else
        reportStatus(
          "The chat prefill is too long. Shorten it to 500 characters or fewer.",
        );
    }
    if (url.searchParams.has("compose")) {
      url.searchParams.delete("compose");
      window.history.replaceState(null, "", url);
    }
  }, [composer]);

  useEffect(() => {
    chatCommands()
      .then(setCommands)
      .catch(() => {});
    flockList()
      .then(setFlocks)
      .catch(() => {});
    peopleList()
      .then(setPeople)
      .catch(() => {});
    agentStatus()
      .then((s) => setConsultReady(s.provider !== "mock"))
      .catch(() => {});
    personaList()
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

  // two popup modes: the command token ("/bri"), and the slug argument right
  // after a command that takes one — the hard-to-recall half of the
  // invocation. A command absent from argRosters gets no argument popup.
  const argRosters: Record<string, ArgItem[]> = { as: personas, flock: flocks };
  const cmdToken = /^\/[a-z]*$/i.test(text)
    ? text.slice(1).toLowerCase()
    : null;
  const arg = argQuery(text, Object.keys(argRosters));
  const argRoster = arg ? argRosters[arg.cmd] : undefined;
  const at = mentionQuery(text);
  const resetKey = at
    ? `@${at.atStart ? "^" : ""}${at.token}`
    : arg
      ? `${arg.cmd}:${arg.token}`
      : cmdToken;
  const [prevToken, setPrevToken] = useState(resetKey);
  if (prevToken !== resetKey) {
    setPrevToken(resetKey);
    setSel(0);
    setDismissed(false);
  }

  const mentionRows: SlashCommand[] = !at
    ? []
    : [
        // a name with a space cannot be written as one @token, so the backend
        // can never match it (services/mentions.py). Suggesting one produces
        // a mention that silently notifies nobody.
        ...people
          .filter(
            (p) =>
              p.kind !== "agent" &&
              MENTIONABLE.test(p.name) &&
              p.name.toLowerCase().startsWith(at.token),
          )
          .map((p) => ({
            name: p.name,
            args: "",
            description: "",
            mention: p.name,
            group: "People",
          })),
        // a leading @slug is the deterministic handoff (/as) and is offered
        // on every provider; a mid-sentence slug reaches the bench through
        // the orchestrator's consult tool, so those rows appear only when a
        // real provider answers the chat (agentStatus above)
        ...(at.atStart || consultReady
          ? personas
              .filter((x) => x.slug.startsWith(at.token))
              .map((x) => ({
                name: x.slug,
                args: "",
                description: `${x.emoji} ${x.description}`,
                mention: x.slug,
                group: "Specialists",
              }))
          : []),
      ];

  const matches: SlashCommand[] = at
    ? mentionRows
    : argRoster && arg
      ? argRoster
          .filter((x) => x.slug.startsWith(arg.token))
          .map((x) => ({
            name: `${arg.cmd} ${x.slug}`,
            args: "<message>",
            description: `${x.emoji} ${x.description}`,
          }))
      : cmdToken === null
        ? []
        : // an exact match leads, whatever the catalog order. `flock` is a
          // strict prefix of `flocks`, so prefix order alone put `/flocks`
          // first for someone who had typed `/flock` in full — Tab rewrote it
          // and Enter SENT the wrong command. agents/commands.py carries the
          // same note for the backend did-you-mean, which has the same hazard.
          commands
            .filter((c) => c.name.startsWith(cmdToken))
            .sort(
              (a, b) =>
                Number(b.name === cmdToken) - Number(a.name === cmdToken),
            );
  const open = !dismissed && !recalling && matches.length > 0;
  // one clamp for Enter, aria-selected and aria-activedescendant: filtering
  // can shrink matches below sel, and a raw sel then points activedescendant
  // at an id that is not in the DOM while Enter runs a different row
  const activeIdx = Math.min(sel, matches.length - 1);
  const active = matches[activeIdx];
  // grouped for rendering, carrying the index into `matches` with each row:
  // a per-section index would break `cmd-${i}` against aria-activedescendant
  const sections: { group?: string; rows: { c: SlashCommand; i: number }[] }[] =
    [];
  matches.forEach((c, i) => {
    const tail = sections[sections.length - 1];
    if (tail && tail.group === c.group) tail.rows.push({ c, i });
    else sections.push({ group: c.group, rows: [{ c, i }] });
  });

  // the listbox has its own scroller (max-h-72 below), so ArrowUp can walk the
  // selection out of view — activedescendant moves, the row does not
  useEffect(() => {
    if (open)
      document
        .getElementById(`cmd-${activeIdx}`)
        ?.scrollIntoView({ block: "nearest" });
  }, [open, activeIdx]);

  const run = (c: SlashCommand) => {
    if (c.mention) {
      // splice at the @token rather than replacing the composer: unlike a
      // command, a mention sits inside a sentence still being written
      // function replacer, so this stays correct if MENTIONABLE above is ever
      // widened: `$&`, `$'` or `$1` in a name would expand as replacement
      // patterns and splice the wrong text
      composer.setText(
        text.replace(
          /(^|\s)@[a-z0-9._-]*$/i,
          (_m, lead) => `${lead}@${c.mention} `,
        ),
      );
      return;
    }
    // "/as <persona>" is the one roster pick that enters a MODE outliving the
    // turn, so it activates the persona and empties the box. Every other one
    // ("/flock <slug>") is per-turn and falls through to the c.args branch,
    // which fills the slug and leaves the caret for the message.
    if (arg?.cmd === "as") {
      const p = findPersona(c.name.slice(arg.cmd.length + 1));
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
    // a MODIFIED arrow belongs to the textarea: Shift+Up extends a selection,
    // Alt/Meta+Up jumps the caret. Swallowing those left a multi-line draft
    // uneditable by keyboard, and let Shift+Up replace the whole draft from
    // history.
    const arrow =
      (e.key === "ArrowUp" || e.key === "ArrowDown") &&
      !e.shiftKey &&
      !e.altKey &&
      !e.metaKey &&
      !e.ctrlKey;
    // a bare modifier keydown is not typing, so it must not end a recall walk
    const typing = !["Shift", "Control", "Alt", "Meta"].includes(e.key);
    if (!arrow && typing && recalling) setRecalling(false);
    if (!open) {
      // The popup is closed, so this arrow belongs to input history on the
      // Input below. Recall can land a bare slash command ("/briefing" is a
      // shipped suggestion), which reopens this popup and would then swallow
      // the NEXT arrow — leaving the person stuck on one entry with no way
      // back to their draft. Staying closed until they type again is what
      // keeps recall walking.
      if (arrow) setRecalling(true);
      return;
    }
    // preventDefault here also holds input history off: the recall handler on
    // the Input yields to an already-prevented event, so an arrow that moves
    // the popup selection must never also reach it.
    if (arrow) e.preventDefault();
    if (e.key === "ArrowDown") {
      setSel((s) => (s + 1) % matches.length);
    } else if (e.key === "ArrowUp") {
      setSel((s) => (s - 1 + matches.length) % matches.length);
    } else if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      e.stopPropagation();
      if (active) run(active);
    } else if (e.key === "Tab") {
      e.preventDefault();
      // a mention completes the same way it is picked — "/name " would be a
      // command that does not exist
      if (active?.mention) run(active);
      else if (active)
        composer.setText(`/${active.name}${active.args ? " " : ""}`);
    } else if (e.key === "Escape") {
      e.stopPropagation();
      setDismissed(true);
    }
  };

  return (
    <div className="relative" onKeyDownCapture={onKeyDownCapture}>
      {open && (
        <div className="absolute inset-x-0 bottom-full mb-2 overflow-hidden rounded-xl border border-line bg-card shadow-float">
          {/* OUTSIDE the listbox: a paragraph is not a role a listbox may own,
              and aria-activedescendant means a screen reader reads only the
              active option — so the one line that teaches the keys reached
              nobody. Spoken now through aria-describedby on the input. */}
          <p
            id="cmd-hint"
            className="border-b border-line px-3 py-1.5 font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-ink-3"
          >
            {at
              ? "Mentions — ↵ or tab to insert"
              : "Commands — ↵ to run, tab to complete"}
          </p>
          {/* max-h + scroll, NOT the outer overflow-hidden alone: the popup is
              anchored bottom-full above a sticky composer, so a roster longer
              than the space above it put rows off the top of the window with
              no way to reach them — measured at -106px on a 520px viewport,
              while ArrowUp still walked the selection onto them. */}
          <div
            id="cmd-list"
            role="listbox"
            aria-label={at ? "Mentions" : "Commands"}
            className="max-h-72 overflow-y-auto"
          >
            {sections.map((sec) => (
              <div
                key={sec.group ?? "flat"}
                role={sec.group ? "group" : "presentation"}
                aria-labelledby={sec.group ? `cmd-grp-${sec.group}` : undefined}
              >
                {/* a real group with a real label, not an aria-hidden heading
                    plus aria-label on each row: aria-label REPLACES the
                    accessible name, so a specialist row announced its section
                    and dropped the description that is the whole row */}
                {sec.group && (
                  <p
                    id={`cmd-grp-${sec.group}`}
                    className="px-3 pb-0.5 pt-2 font-mono text-[10px] uppercase tracking-[0.12em] text-ink-3"
                  >
                    {sec.group}
                  </p>
                )}
                {sec.rows.map(({ c, i }) => (
                  <button
                    key={c.name}
                    id={`cmd-${i}`}
                    role="option"
                    aria-selected={i === activeIdx}
                    onMouseEnter={() => setSel(i)}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => run(c)}
                    className={`flex w-full items-baseline gap-2 px-3 py-2 text-left text-sm ${
                      i === activeIdx ? "bg-thread/10" : ""
                    }`}
                  >
                    <code className="shrink-0 font-medium text-thread">
                      {c.mention ? `@${c.name}` : `/${c.name}`}
                    </code>
                    {c.args && (
                      <code className="shrink-0 text-xs text-ink-3">
                        {c.args}
                      </code>
                    )}
                    <span className="truncate text-xs text-ink-3">
                      {c.description}
                    </span>
                    {i === activeIdx && (
                      <kbd
                        aria-hidden="true"
                        className="ml-auto shrink-0 rounded border border-line-strong px-1 font-mono text-[10px] text-ink-3"
                      >
                        ↵
                      </kbd>
                    )}
                  </button>
                ))}
              </div>
            ))}
          </div>
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
          {...inputHistory}
          name="message"
          role="combobox"
          aria-expanded={open}
          aria-controls={open ? "cmd-list" : undefined}
          aria-describedby={open ? "cmd-hint" : undefined}
          aria-autocomplete="list"
          aria-activedescendant={open ? `cmd-${activeIdx}` : undefined}
          autoFocus
          placeholder={
            activePersona
              ? `Message ${activePersona.name}…`
              : "Message the Chief of Staff… (/help for commands, or ask in your own words)"
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
  // the same store the Composer reads: a sticky persona (set by /agents'
  // bench cards via ?as=, and restored from sessionStorage for every new
  // thread) prefixes freeform messages with `/as <slug>`, so the empty
  // state's "goes to the Chief of Staff" is false exactly then — while the
  // chip above the composer says so on the same screen
  const activePersona = useSyncExternalStore(
    subscribePersona,
    getActivePersona,
    () => null,
  );
  return (
    <ThreadPrimitive.Root className="flex min-h-0 flex-1 flex-col">
      <ThreadPrimitive.Viewport className="flex flex-1 flex-col overflow-y-auto px-4 pt-4 lg:px-8">
        <ThreadPrimitive.Empty>
          <div className="mx-auto flex max-w-lg flex-1 flex-col items-center justify-center pb-24 text-center">
            <div className="loom-idle mb-6 w-40" aria-hidden />
            <p className="font-display text-2xl font-semibold tracking-tight text-ink">
              Skein Chief of Staff
            </p>
            <p className="mt-2 text-sm text-ink-3">
              Track milestones, log questions, record decisions, post standups,
              and plan projects — ask in your own words.
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
              instantly, no model needed. <code>/personas</code> lists the bench
              of specialists, and <code>/as</code> calls one.{" "}
              <code>/flocks</code> lists the groups of them, and{" "}
              <code>/flock</code> asks a whole group at one time.{" "}
              {activePersona
                ? `Every other message goes to ${activePersona.name}, the persona above the composer.`
                : "Every other message goes to the Chief of Staff."}
            </p>
          </div>
        </ThreadPrimitive.Empty>
        {/* the reading column lives INSIDE the scroller: the scrollbar and the
            composer gradient stay at the pane edge, and toggling the sidebar
            can't slide the conversation sideways */}
        <div className="mx-auto w-full max-w-3xl">
          <ThreadPrimitive.Messages
            components={{ UserMessage, AssistantMessage }}
          />
        </div>
        <ThreadPrimitive.ViewportFooter className="sticky bottom-0 mt-auto bg-gradient-to-t from-page via-page to-transparent pb-4 pt-2">
          <div className="mx-auto w-full max-w-3xl">
            <Composer />
          </div>
        </ThreadPrimitive.ViewportFooter>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
}
