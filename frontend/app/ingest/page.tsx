"use client";

import Link from "next/link";
import { useState } from "react";

import { api } from "@/lib/api";

type IngestResult = {
  proposals: { id: number; kind: string; line: string }[];
  unclassified: string[];
  skipped_private: number;
};

export default function IngestPage() {
  const [text, setText] = useState("");
  const [result, setResult] = useState<IngestResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api<IngestResult>("/api/ingest", {
        method: "POST",
        body: JSON.stringify({ text }),
      });
      setResult(r);
      setText("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto w-full max-w-3xl p-6">
      <h1 className="mb-1 text-xl font-bold">Ingest meeting notes</h1>
      <p className="mb-4 text-sm text-zinc-500">
        Paste raw notes. Lines that match the capture grammar (todo:, q:,
        decision:, blocked…, promised:) become <b>review proposals</b> — nothing
        is written directly. <code>fb:</code> lines are skipped, never stored.
      </p>

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={12}
        placeholder={
          "- todo: update the runbook\n- q: who owns the staging cluster?\n- decided: we ship Fridays\n- blocked on the API key from vendor\n- promised: revised beta date to ops by Friday\n- note: retro moved to Thursdays"
        }
        className="mb-3 w-full rounded-xl border border-zinc-300 bg-transparent p-3 font-mono text-sm outline-none dark:border-zinc-700"
      />
      <button
        onClick={run}
        disabled={busy || !text.trim()}
        className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700 disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900"
      >
        {busy ? "Ingesting…" : "Ingest"}
      </button>

      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

      {result && (
        <div className="mt-6 space-y-4 text-sm">
          <p>
            ✅ {result.proposals.length} proposal(s) created —{" "}
            <Link href="/review" className="font-medium underline">
              review them
            </Link>
            {result.skipped_private > 0 && (
              <span className="ml-2 text-amber-600">
                · {result.skipped_private} fb: line(s) skipped (private — use ⌘K
                with your key)
              </span>
            )}
          </p>
          <ul className="space-y-1">
            {result.proposals.map((p) => (
              <li key={p.id} className="text-zinc-600 dark:text-zinc-300">
                <span className="mr-2 rounded bg-zinc-100 px-1.5 py-0.5 text-xs dark:bg-zinc-800">
                  {p.kind}
                </span>
                {p.line}
              </li>
            ))}
          </ul>
          {result.unclassified.length > 0 && (
            <div>
              <p className="mb-1 font-medium text-zinc-500">
                Not captured ({result.unclassified.length}) — add a prefix and
                re-paste if any matter:
              </p>
              <ul className="ml-4 list-disc text-zinc-400">
                {result.unclassified.slice(0, 20).map((l, i) => (
                  <li key={i}>{l}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </main>
  );
}
