"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { actionError, api } from "@/lib/api";

// mirrors backend/app/services/capture.py PATTERNS — the preview must tell
// the truth about where a line will land, so the grammar is afforded, not
// memorized
const RULES: [string, RegExp][] = [
  ["question", /^\s*(q:|question:)/i],
  ["blocker", /^\s*(blocked|blocker|stuck)\b[:\s]/i],
  ["decision", /^\s*(decision:|decided\b)/i],
  ["commitment", /^\s*(promised?:|commitment:)/i],
  ["request", /^\s*(req:|request:)/i],
  ["task", /^\s*(todo:|task:)/i],
  ["note", /^\s*(note:|fyi:|til:)/i],
  ["question", /\?\s*$/],
  ["blocker", /\b(blocked (by|on)|waiting on)\b/i],
  ["decision", /\bwe (decided|chose|are going with)\b/i],
  ["commitment", /\bwe (promised|committed to)\b/i],
  ["task", /^\s*(fix|add|update|implement|write|ship|review|schedule)\b/i],
];

const KNOWN_PREFIX =
  /^\s*(q|question|todo|task|note|fyi|til|decision|blocker|blocked|stuck|promised?|commitment|req|request|fb):\s*/i;

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
  { prefix: "req:", label: "request" },
  { prefix: "note:", label: "note" },
  { prefix: "fb:", label: "private feedback" },
];

export function CapturePalette() {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  // mirrors `text` for the once-mounted window listener below, which would
  // otherwise read the first render's empty string forever
  const textRef = useRef("");
  useEffect(() => {
    textRef.current = text;
  }, [text]);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const openerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (closeTimer.current) clearTimeout(closeTimer.current);
        setOpen((o) => {
          if (!o) openerRef.current = document.activeElement as HTMLElement | null;
          return !o;
        });
        setResult(null);
      }
      if (e.key === "Escape") {
        // same rule as the backdrop: never discard typed text. The first
        // Escape clears the draft, the second closes — one rule for both
        // gestures instead of a reflex that loses a sentence.
        if (textRef.current.trim()) setText("");
        else setOpen(false);
      }
    };
    // the nav's ⌘K button dispatches this for touch/voice users — keyboard
    // isn't the only door into quick capture
    const onOpen = () => {
      if (closeTimer.current) clearTimeout(closeTimer.current);
      openerRef.current = document.activeElement as HTMLElement | null;
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

  // dialog contract: focus returns to wherever ⌘K was pressed
  useEffect(() => {
    if (!open) openerRef.current?.focus();
  }, [open]);

  const trapTab = (e: React.KeyboardEvent) => {
    if (e.key !== "Tab" || !dialogRef.current) return;
    const focusables = dialogRef.current.querySelectorAll<HTMLElement>(
      "button:not([disabled]), textarea, [tabindex]",
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
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      const r = await api<{ kind: string; id: number }>("/api/capture", {
        method: "POST",
        body: JSON.stringify({ text }),
      });
      setResult(`Captured as ${r.kind} #${r.id}`);
      setText("");
      // long enough for the live region to announce before the dialog goes
      closeTimer.current = setTimeout(() => setOpen(false), 1400);
    } catch (err) {
      setResult(`⚠️ ${actionError(err)}`);
    } finally {
      setBusy(false);
    }
  }, [text, busy]);

  const applyChip = (prefix: string) => {
    setText((t) => `${prefix} ${t.replace(KNOWN_PREFIX, "").trimStart()}`);
    inputRef.current?.focus();
  };

  if (!open) return null;
  const kind = text.trim() ? previewKind(text) : "";
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 px-4 pt-16 sm:pt-32"
      onClick={() => {
        // most of a phone screen is backdrop — never discard typed text
        if (!text.trim()) setOpen(false);
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
        <p className="mb-2 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
          Quick capture
        </p>
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
                  : `will file as: ${kind}${kind === "private feedback" ? " (needs your API key)" : ""}`
                : [
                    <span key="kbd" className="[@media(any-pointer:coarse)]:hidden">
                      Enter to save · Esc to close
                    </span>,
                    <span key="touch" className="[@media(any-pointer:fine)]:hidden">
                      Capture to save · tap outside to close
                    </span>,
                  ])}

          </span>
          <button
            onClick={submit}
            disabled={busy || !text.trim()}
            className="rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
          >
            Capture
          </button>
        </div>
        <div className="mt-3 border-t border-line pt-2 text-[11px] leading-relaxed text-ink-3">
          Tap a chip or type a prefix — the line above the button always shows
          where your text will land. <code>req:</code> files a request for
          triage; <code>fb: name — …</code> stays private to you.
        </div>
      </div>
    </div>
  );
}
