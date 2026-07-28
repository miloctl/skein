"use client";

import Link from "next/link";
import { useState } from "react";

import { api } from "@/lib/api";
import { SectionTabs } from "@/components/section-tabs";

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
  const [filed, setFiled] = useState<Record<string, string>>({});
  const [picks, setPicks] = useState<Record<string, string>>({});

  // an unmatched line gets filed by re-running it through the same
  // proposals-only pipeline with the chosen prefix — never a direct write
  const fileLine = async (key: string, line: string, prefix: string) => {
    try {
      const r = await api<IngestResult>("/api/ingest", {
        method: "POST",
        body: JSON.stringify({ text: `${prefix} ${line}` }),
      });
      const kind = r.proposals[0]?.kind ?? "proposal";
      setFiled((f) => ({ ...f, [key]: kind }));
    } catch (e) {
      setError(String(e));
    }
  };

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
      setFiled({});
      setPicks({});
      setText("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto w-full max-w-5xl p-4 sm:p-6">
      <SectionTabs set="inbox" />
      <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">Paste meeting notes</h1>
      <p className="mb-6 text-sm text-ink-3">
        Lines that start with a prefix (todo:, q:, decision:, blocked on …,
        promised:) become <b>review proposals</b> — nothing is written
        directly. <code>fb:</code> lines are skipped, never stored.
      </p>

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={12}
        placeholder={
          "- todo: update the runbook\n- q: who owns the staging cluster?\n- decided: we ship Fridays\n- blocked on the API key from vendor\n- promised: revised beta date to ops by Friday\n- note: retro moved to Thursdays"
        }
        className="mb-3 w-full rounded-xl border border-line-strong bg-transparent p-3 font-mono text-sm outline-none focus:border-thread-solid"
      />
      <button
        onClick={run}
        disabled={busy || !text.trim()}
        className="rounded-lg bg-thread-solid px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
      >
        {busy ? "Extracting…" : "Extract proposals"}
      </button>

      {error && <p className="mt-3 text-sm text-danger">{error}</p>}

      {result && (
        <div className="mt-6 space-y-4 text-sm">
          <p>
            ✅ {result.proposals.length} proposal(s) created —{" "}
            <Link href="/review" className="font-medium underline">
              review them
            </Link>
            {result.skipped_private > 0 && (
              <span className="ml-2 text-weld">
                · {result.skipped_private} fb: line(s) skipped (private — use ⌘K
                with your key)
              </span>
            )}
          </p>
          <ul className="space-y-1">
            {result.proposals.map((p) => (
              <li key={p.id} className="text-ink-2">
                <span className="mr-2 rounded bg-raised px-1.5 py-0.5 text-xs">
                  {p.kind}
                </span>
                {p.line}
              </li>
            ))}
          </ul>
          {result.unclassified.length > 0 && (
            <div>
              <p className="mb-1 font-medium text-ink-3">
                Not captured ({result.unclassified.length}) — file any that
                matter, right here:
              </p>
              <ul className="space-y-1 text-ink-3">
                {result.unclassified.slice(0, 20).map((l, i) => { const key = `${i}:${l}`; return (
                  <li key={key} className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate" title={l}>
                      {l}
                    </span>
                    {filed[key] ? (
                      <span role="status" className="text-xs text-ok">
                        ✓ proposed as {filed[key]}
                      </span>
                    ) : (
                      <span className="flex items-center gap-1">
                        <select
                          value={picks[key] ?? ""}
                          aria-label={`File "${l}" as`}
                          onChange={(e) => setPicks((p) => ({ ...p, [key]: e.target.value }))}
                          className="rounded border border-line-strong bg-card px-1.5 py-0.5 text-xs"
                        >
                          <option value="" disabled>
                            file as…
                          </option>
                          <option value="todo:">task</option>
                          <option value="q:">question</option>
                          <option value="decision:">decision</option>
                          <option value="promised:">promise</option>
                          <option value="blocked on">blocker</option>
                          <option value="req:">request</option>
                          <option value="note:">note</option>
                        </select>
                        <button
                          disabled={!picks[key]}
                          onClick={() => picks[key] && fileLine(key, l, picks[key])}
                          className="rounded bg-thread-solid px-2 py-0.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-40"
                        >
                          file
                        </button>
                      </span>
                    )}
                  </li>
                ); })}
              </ul>
            </div>
          )}
        </div>
      )}
    </main>
  );
}
