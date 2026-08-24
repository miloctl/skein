"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { VisibilityPicker } from "@/components/visibility-picker";
import { actionError, api, getUser, isUnreachable, subscribeUser } from "@/lib/api";
import { notifyAttentionChange } from "@/lib/attention";
import { isGated, subscribeGated } from "@/lib/gated";

// mirrors backend/app/services/capture.py PATTERNS — the preview must tell
// the truth about where a line will land, so the grammar is afforded, not
// memorized
const RULES: [string, RegExp][] = [
  ["question", /^\s*(q:|question:)/i],
  ["blocker", /^\s*(blocked|blocker|stuck)\b[:\s]/i],
  ["decision", /^\s*(decision:|decided\b)/i],
  ["promise", /^\s*(promised?:|commitment:)/i],
  // BEFORE the `waiting on` blocker heuristic below, which would otherwise
  // claim the same sentence. Without this rule the preview said "note" while
  // the backend (services/capture.py) filed a received promise.
  // `waiting for:` and `waiting on` are one phrase to a reader and opposite
  // entities to the parser, so only the COLON form routes here — the bare
  // `waiting on` heuristic below still means blocker, and docs/LEXICON.md
  // settles the colon form as a promise
  ["awaiting", /^\s*(awaiting|waiting for):/i],
  ["request", /^\s*(req:|request:)/i],
  ["task", /^\s*(todo:|task:)/i],
  ["note", /^\s*(note:|fyi:|til:)/i],
  ["question", /\?\s*$/],
  ["blocker", /\b(blocked (by|on)|waiting on)\b/i],
  ["decision", /\bwe (decided|chose|are going with)\b/i],
  ["promise", /\bwe (promised|committed to)\b/i],
  ["task", /^\s*(fix|add|update|implement|write|ship|review|schedule)\b/i],
];

// `commitment:` in the regexes above is an INPUT alias only (typed muscle
// memory keeps working); the kind on the wire and on screen is promise
// (docs/LEXICON.md row 1)
const KNOWN_PREFIX =
  /^\s*(q|question|todo|task|note|fyi|til|decision|blocker|blocked|stuck|promised?|commitment|awaiting|waiting for|req|request|fb):\s*/i;

function readyToCapture(text: string): boolean {
  if (!text.trim() || /^\s*blocked on\s*$/i.test(text)) return false;
  const prefix = KNOWN_PREFIX.exec(text);
  return prefix === null || Boolean(text.slice(prefix[0].length).trim());
}

function previewKind(text: string): string {
  const lines = text.split("\n");
  // backend hard-rejects fb: buried in multi-line text — the preview must
  // say so, not claim "task" and then 400
  if (lines.some((l) => /^\s*fb:/i.test(l))) {
    return lines.filter((l) => l.trim()).length > 1
      ? "⚠ will not file — fb: must be captured alone"
      : "private feedback";
  }
  for (const [kind, re] of RULES) if (re.test(text)) return kind;
  return "note";
}

const CHIPS: { prefix: string; label: string }[] = [
  { prefix: "todo:", label: "task" },
  { prefix: "q:", label: "question" },
  { prefix: "blocked on", label: "blocker" },
  { prefix: "decision:", label: "decision" },
  { prefix: "promised:", label: "promise" },
  { prefix: "awaiting:", label: "awaiting" },
  { prefix: "req:", label: "request" },
  { prefix: "note:", label: "note" },
  { prefix: "fb:", label: "private feedback" },
];

export function CapturePalette() {
  const [open, setOpen] = useState(false);
  const gated = useSyncExternalStore(subscribeGated, isGated, () => false);
  // nav.tsx renders the search box only for a named visitor, and `gated` is a
  // DIFFERENT condition (the auth gate standing in for the page) — so an
  // anonymous visitor reaches this dialog with no search box in the nav. The
  // footer must not send them to a control that is not on their screen.
  const user = useSyncExternalStore(subscribeUser, getUser, () => "anonymous");
  const canSearch = user !== "anonymous";
  const [text, setText] = useState("");
  const [generatedDraft, setGeneratedDraft] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [tier, setTier] = useState({ visibility: "workspace", crew_id: 0 });
  const [busy, setBusy] = useState(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const placeCaretRef = useRef(false);
  const generatedDraftRef = useRef("");
  // mirrors `text` for the once-mounted window listener below, which would
  // otherwise read the first render's empty string forever
  const textRef = useRef("");
  useEffect(() => {
    textRef.current = text;
  }, [text]);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const receiptRef = useRef<{
    kind: string;
    id: number;
    firstWatchGeneration?: number;
  } | null>(null);
  const firstWatchGenerationRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    // No open-shortcut here. ⌘K belongs to search (components/nav-search.tsx),
    // which is where every other product puts it, and capture is reached by
    // its own button. Escape still belongs to this dialog while it is open.
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        // A generated First Watch prefix is not a person's draft. It closes in
        // one gesture; text they added keeps the existing clear-then-close rule.
        const generated = textRef.current === generatedDraftRef.current;
        if (textRef.current.trim() && !generated) {
          generatedDraftRef.current = "";
          setGeneratedDraft("");
          setText("");
        } else {
          generatedDraftRef.current = "";
          setGeneratedDraft("");
          setText("");
          setOpen(false);
        }
      }
    };
    // the nav's Capture button dispatches this: it is the only door into
    // quick capture now that the keystroke names search
    const onOpen = (event: Event) => {
      if (closeTimer.current) clearTimeout(closeTimer.current);
      openerRef.current = document.activeElement as HTMLElement | null;
      const detail = (
        event as CustomEvent<{ text?: string; firstWatchGeneration?: number }>
      ).detail;
      const initial = detail?.text;
      firstWatchGenerationRef.current = detail?.firstWatchGeneration;
      generatedDraftRef.current = "";
          setGeneratedDraft("");
      if (!textRef.current && initial) {
        generatedDraftRef.current = initial;
        setGeneratedDraft(initial);
        placeCaretRef.current = true;
        setText(initial);
      }
      setResult(null);
      setOpen(true);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("skein-capture-open", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("skein-capture-open", onOpen);
      if (closeTimer.current) clearTimeout(closeTimer.current);
    };
  }, []);

  // dialog contract: focus returns to whatever opened the palette
  useEffect(() => {
    if (open) return;
    openerRef.current?.focus();
    const receipt = receiptRef.current;
    if (!receipt) return;
    receiptRef.current = null;
    firstWatchGenerationRef.current = undefined;
    window.dispatchEvent(new CustomEvent("skein-capture-complete", { detail: receipt }));
  }, [open]);

  useEffect(() => {
    if (!open || !placeCaretRef.current || !inputRef.current) return;
    placeCaretRef.current = false;
    inputRef.current.focus();
    inputRef.current.setSelectionRange(text.length, text.length);
  }, [open, text]);

  useEffect(() => {
    if (!open || gated) return;
    const changed = [...document.body.children].filter(
      (element) =>
        !element.contains(dialogRef.current) && !element.hasAttribute("inert"),
    ) as HTMLElement[];
    changed.forEach((element) => element.setAttribute("inert", ""));
    return () => changed.forEach((element) => element.removeAttribute("inert"));
  }, [gated, open]);

  const trapTab = (e: React.KeyboardEvent) => {
    if (e.key !== "Tab" || !dialogRef.current) return;
    // `select` and `input` are in this list because the dialog gained a
    // control that is neither a button nor a textarea. The visibility picker
    // mounts BELOW the Capture button, so with the old selector the last
    // focusable was always Capture, Tab from it wrapped to the first chip,
    // and the one control that decides who can read the capture was
    // unreachable by keyboard — every keyboard-only capture went to the
    // workspace tier with no way to see the choice existed.
    // Anything focusable added here must match, or it is invisible the same way.
    const focusables = dialogRef.current.querySelectorAll<HTMLElement>(
      "button:not([disabled]), textarea, select, input:not([type=hidden]), a[href], [tabindex]",
    );
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  const submit = useCallback(async () => {
    if (!readyToCapture(text) || busy) return;
    setBusy(true);
    try {
      const r = await api<{ kind: string; id: number }>("/api/capture", {
        method: "POST",
        body: JSON.stringify({ text, ...tier }),
      });
      notifyAttentionChange();
      receiptRef.current = {
        ...r,
        ...(firstWatchGenerationRef.current === undefined
          ? {}
          : { firstWatchGeneration: firstWatchGenerationRef.current }),
      };
      setResult(`Captured as ${r.kind} #${r.id}`);
      generatedDraftRef.current = "";
          setGeneratedDraft("");
      setText("");
      // the tier resets with the text. The dialog closes after each capture,
      // so a tier left behind is a tier nobody can see — the next unrelated
      // thought was filed to the crew the last one chose.
      setTier({ visibility: "workspace", crew_id: 0 });
      // long enough for the live region to announce before the dialog goes
      closeTimer.current = setTimeout(() => setOpen(false), 1400);
    } catch (err) {
      const recovery = isUnreachable(err)
        ? " Search for the record before you try again."
        : "";
      setResult(`⚠️ ${actionError(err)}${recovery}`);
    } finally {
      setBusy(false);
    }
    // tier IS a dependency: without it the callback closes over the tier as it
    // was when the text last changed, so choosing a crew AFTER typing filed the
    // capture at workspace — and the picker sits below the textarea, which
    // makes type-then-choose the natural order
  }, [text, busy, tier]);

  const applyChip = (prefix: string) => {
    setText((t) => `${prefix} ${t.replace(KNOWN_PREFIX, "").trimStart()}`);
    inputRef.current?.focus();
  };

  // the gate out-ranks nothing on z-index (this is z-50, the gate z-30), so
  // without this the Capture button opened a box over the gate that could
  // only ever answer 401
  if (!open || gated) return null;
  const kind = text.trim() ? previewKind(text) : "";
  const hasRealDraft = Boolean(
    text.trim() && text !== generatedDraft,
  );
  const closeOrClear = () => {
    if (hasRealDraft) {
      generatedDraftRef.current = "";
          setGeneratedDraft("");
      setText("");
      return;
    }
    generatedDraftRef.current = "";
          setGeneratedDraft("");
    setText("");
    setOpen(false);
  };
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto overscroll-contain bg-black/30 px-4 py-4 sm:pt-32"
      onClick={() => {
        // most of a phone screen is backdrop — never discard text the person added
        if (!text.trim() || text === generatedDraftRef.current) {
          generatedDraftRef.current = "";
          setGeneratedDraft("");
          setText("");
          setOpen(false);
        }
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Quick capture"
        className="w-full max-w-lg rounded-xl border border-line-strong bg-card p-4 shadow-float"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={trapTab}
      >
        <div className="mb-2 flex items-center justify-between gap-2">
          <p className="font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
            Quick capture
          </p>
          <button
            type="button"
            onClick={closeOrClear}
            className="min-h-6 rounded px-2 py-1 text-xs text-ink-2 hover:bg-raised hover:text-ink"
          >
            {hasRealDraft ? "Clear draft" : "Close"}
          </button>
        </div>
        <div className="mb-2 flex flex-wrap gap-1">
          {CHIPS.map((c) => (
            <button
              key={c.prefix}
              onClick={() => applyChip(c.prefix)}
              title={`Start with “${c.prefix}”`}
              className="rounded-full bg-raised px-2.5 py-1.5 text-[11px] text-ink-2 hover:bg-line hover:text-ink md:px-2 md:py-0.5"
            >
              {c.label}
            </button>
          ))}
        </div>
        <textarea
          autoFocus
          ref={inputRef}
          name="capture-text"
          aria-label="What to capture"
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="todo: ship the API · why is staging down? · blocked on vendor…"
          className="w-full resize-none rounded-lg border border-line-strong bg-transparent p-2 text-sm outline-none focus:border-thread-solid"
        />
        <div className="mt-2 flex items-center justify-between">
          <span
            role={result?.startsWith("⚠️") ? "alert" : "status"}
            aria-live="polite"
            className="text-xs text-ink-3"
          >
            {result ??
              (kind
                ? kind.startsWith("⚠")
                  ? kind
                  : `will file as: ${kind}${kind === "private feedback" ? " (requires strong identity)" : ""}`
                : [
                    <span
                      key="kbd"
                      className="[@media(any-pointer:coarse)]:hidden"
                    >
                      Enter to save · Esc to close
                    </span>,
                    <span
                      key="touch"
                      className="[@media(any-pointer:fine)]:hidden"
                    >
                      Capture to save · tap outside to close
                    </span>,
                  ])}
          </span>
          <button
            onClick={submit}
            disabled={busy || !readyToCapture(text)}
            className="rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
          >
            Capture
          </button>
        </div>
        <div className="mt-2">
          {/* An `fb:` capture short-circuits in services/capture.py BEFORE the
              tier is read, into the private schema — one no other code
              path opens. The tier IS still on the wire — submit sends the
              whole state — and the server discards it, so a picker reading
              "Platform only" would state a choice that has no effect. It
              fails safe (more private, not less), which is exactly why it
              would never be noticed. */}
          {previewKind(text) === "private feedback" ? (
            <p className="text-xs text-ink-3">
              Visible to <span className="text-ink-2">only you</span> — feedback
              is kept out of the shared record, so it takes no other choice.
            </p>
          ) : (
            <VisibilityPicker value={tier} onChange={setTier} label="capture" />
          )}
        </div>
        <div className="mt-3 border-t border-line pt-2 text-[11px] leading-relaxed text-ink-3">
          Tap a chip or type a prefix — the line above the button always shows
          where your text will land. No prefix files a note. <code>req:</code>{" "}
          files a request for triage. <code>fb: name — …</code> stays private to you.
          {/* The read/write split is the reported confusion: this dialog and
              the nav search box look alike and do opposite things. Naming the
              other door here is what keeps a search from being typed into the
              one input that FILES what it is given. */}
          {canSearch ? (
            <>
              {" "}
              Quick capture writes new records only. To find a record that
              exists, use the search box in the top bar.
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
