"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";

// mirrors backend/app/services/capture.py PATTERNS — the preview must tell
// the truth about where a line will land, so the grammar is afforded, not
// memorized
const RULES: [string, RegExp][] = [
  ["question", /^\s*(q:|question:)/i],
  ["question", /\?\s*$/],
  ["blocker", /^\s*(blocked|blocker|stuck)[:\s]/i],
  ["blocker", /\b(blocked (by|on)|waiting on)\b/i],
  ["decision", /^\s*(decision:|decided\b)/i],
  ["decision", /\bwe (decided|chose|are going with)\b/i],
  ["commitment", /^\s*(promised?:|commitment:)/i],
  ["commitment", /\bwe (promised|committed to)\b/i],
  ["request", /^\s*(req:|request:)/i],
  ["task", /^\s*(todo:|task:)/i],
  ["task", /^\s*(fix|add|update|implement|write|ship|review|schedule)\b/i],
  ["note", /^\s*(note:|fyi:|til:)/i],
];

const KNOWN_PREFIX =
  /^\s*(q|question|todo|task|note|fyi|til|decision|blocker|blocked|stuck|promised?|commitment|req|request|fb):\s*/i;

function previewKind(text: string): string {
  if (/^\s*fb:/i.test(text)) return "private feedback";
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

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (closeTimer.current) clearTimeout(closeTimer.current);
        setOpen((o) => !o);
        setResult(null);
      }
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      if (closeTimer.current) clearTimeout(closeTimer.current);
    };
  }, []);

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
      closeTimer.current = setTimeout(() => setOpen(false), 900);
    } catch (err) {
      setResult(`⚠️ ${String(err)}`);
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
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 pt-32"
      onClick={() => setOpen(false)}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Quick capture"
        className="w-full max-w-lg rounded-xl border border-line-strong bg-card p-4 shadow-float"
        onClick={(e) => e.stopPropagation()}
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
              className="rounded-full bg-raised px-2 py-0.5 text-[11px] text-ink-2 hover:bg-line hover:text-ink"
            >
              {c.label}
            </button>
          ))}
        </div>
        <textarea
          autoFocus
          ref={inputRef}
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
          <span className="text-xs text-ink-3">
            {result ??
              (kind
                ? `will file as: ${kind}${kind === "private feedback" ? " (needs your API key)" : ""}`
                : "Enter to save · Esc to close")}
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
