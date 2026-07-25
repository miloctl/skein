"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";

export function CapturePalette() {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  if (!open) return null;
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
          Quick capture — routed to task / question / note / decision / blocker / commitment
        </p>
        <textarea
          autoFocus
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
          <span className="text-xs text-ink-3">{result ?? "Enter to save · Esc to close"}</span>
          <button
            onClick={submit}
            disabled={busy || !text.trim()}
            className="rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
          >
            Capture
          </button>
        </div>
        <div className="mt-3 border-t border-line pt-2 text-[11px] leading-relaxed text-ink-3">
          <span className="font-medium text-ink-3">Prefixes:</span>{" "}
          <code>todo:</code> task · <code>q:</code> question · <code>decision:</code> decision ·{" "}
          <code>promised:</code> commitment · <code>note:</code>/<code>til:</code> note ·{" "}
          <code>blocked on …</code> blocker ·{" "}
          <code>fb: name — …</code> private feedback (needs your API key — see ⚙️ Settings) ·
          no prefix = smart-routed
        </div>
      </div>
    </div>
  );
}
