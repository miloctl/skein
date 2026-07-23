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
        className="w-full max-w-lg rounded-xl border border-zinc-200 bg-white p-4 shadow-2xl dark:border-zinc-700 dark:bg-zinc-900"
        onClick={(e) => e.stopPropagation()}
      >
        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-400">
          Quick capture — routed to task / question / note / decision / blocker
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
          className="w-full resize-none rounded-lg border border-zinc-300 bg-transparent p-2 text-sm outline-none dark:border-zinc-700"
        />
        <div className="mt-2 flex items-center justify-between">
          <span className="text-xs text-zinc-500">{result ?? "Enter to save · Esc to close"}</span>
          <button
            onClick={submit}
            disabled={busy || !text.trim()}
            className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
          >
            Capture
          </button>
        </div>
      </div>
    </div>
  );
}
