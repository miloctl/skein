"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState, useSyncExternalStore } from "react";

import { actionError, api } from "@/lib/api";
import { subscribeSession, trustedHeaderIdentity } from "@/lib/auth";
import { Shortcut } from "@/components/shortcut";

type IngestResult = {
  proposals: { id: number; kind: string; line: string; already_pending?: boolean }[];
  unclassified: string[];
  skipped_private: number;
};

/** Reports `?event=<id>`, the meeting the notes came from, or 0. In its own
 *  Suspense boundary, as app/notes/page.tsx explains for `?note=`. */
function EventParam({ onChange }: { onChange: (id: number) => void }) {
  const raw = useSearchParams().get("event");
  const id = Number(raw);
  const event = raw && Number.isInteger(id) && id > 0 ? id : 0;
  useEffect(() => onChange(event), [event, onChange]);
  return null;
}

export default function IngestPage() {
  // a weak name reads no private row, so its proposals keep the team queue
  const weak = useSyncExternalStore(subscribeSession, trustedHeaderIdentity, () => false);
  const [text, setText] = useState("");
  const [result, setResult] = useState<IngestResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filed, setFiled] = useState<Record<string, string>>({});
  const [picks, setPicks] = useState<Record<string, string>>({});
  // the meeting from `?event=`, sent with every paste so each record an
  // approval writes links back to it (services/ingest.py::_meeting)
  const [eventId, setEventId] = useState(0);
  const [meeting, setMeeting] = useState<{ id: number; title?: string; error?: string } | null>(
    null,
  );
  const onEvent = useCallback((id: number) => setEventId(id), []);
  useEffect(() => {
    if (!eventId) return;
    let live = true;
    api<{ title: string }>(`/api/events/${eventId}`)
      .then((e) => live && setMeeting({ id: eventId, title: e.title }))
      .catch((e) => live && setMeeting({ id: eventId, error: actionError(e) }));
    return () => {
      live = false;
    };
  }, [eventId]);
  const withMeeting = (body: { text: string }) =>
    JSON.stringify(eventId ? { ...body, event_id: eventId } : body);

  // an unmatched line gets filed by re-running it through the same
  // proposals-only pipeline with the chosen prefix - never a direct write
  const fileLine = async (key: string, line: string, prefix: string) => {
    try {
      const r = await api<IngestResult>("/api/ingest", {
        method: "POST",
        body: withMeeting({ text: `${prefix} ${line}` }),
      });
      const kind = r.proposals[0]?.kind ?? "proposal";
      setFiled((f) => ({ ...f, [key]: kind }));
    } catch (e) {
      setError(actionError(e));
    }
  };

  const run = async () => {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api<IngestResult>("/api/ingest", {
        method: "POST",
        body: withMeeting({ text }),
      });
      setResult(r);
      setFiled({});
      setPicks({});
      setText("");
    } catch (e) {
      setError(actionError(e));
    } finally {
      setBusy(false);
    }
  };

  // a line already pending from an earlier paste is named, never counted as created
  const created = (result?.proposals ?? []).filter((p) => !p.already_pending);
  const pending = (result?.proposals ?? []).filter((p) => p.already_pending);
  return (
    <main id="content" tabIndex={-1} className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6">
      <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">Paste meeting notes</h1>
      <p className="mb-6 max-w-3xl text-sm text-ink-3">
        Lines that start with <code>todo:</code>, <code>q:</code>,{" "}
        <code>decision:</code> or <code>decided:</code>, <code>blocked on</code>,{" "}
        <code>promised:</code>, <code>awaiting:</code>, <code>req:</code>, or{" "}
        <code>note:</code> become <b>review proposals</b>. Plain lines stay under{" "}
        <b>Not captured</b>. Nothing is written directly.{" "}
        {weak
          ? "The proposals go to the team review queue."
          : "The proposals are yours alone to approve, and each approval writes the record at the tier it names."}{" "}
        <code>fb:</code> lines are skipped and never stored.
      </p>
      <Suspense fallback={null}>
        <EventParam onChange={onEvent} />
      </Suspense>
      {eventId > 0 && (
        <p className="mb-3 max-w-3xl text-sm text-ink-2">
          {meeting?.id !== eventId
            ? "Loading the meeting…"
            : meeting.error
              ? meeting.error
              : `These notes are from the meeting "${meeting.title}". Each record links back to it, at the meeting's visibility.`}{" "}
          <button
            onClick={() => {
              setEventId(0);
              window.history.replaceState({}, "", "/ingest");
              // the button leaves with the meeting, and focus must not leave
              // with it: the notes field is where the reader goes next
              document.querySelector<HTMLTextAreaElement>('textarea[name="meeting-notes"]')?.focus();
            }}
            className="font-medium text-thread underline"
          >
            Paste without a meeting
          </button>
        </p>
      )}

      <textarea
        name="meeting-notes"
        value={text}
        onChange={(e) => setText(e.target.value)}
        // placeholder is not a label: it vanishes on the first keystroke and
        // leaves a screen reader with an unnamed field
        aria-label="Paste your notes, one item per line"
        rows={12}
        placeholder={
          "- todo: update the runbook\n- q: who owns the staging cluster?\n- decision: we ship Fridays\n- blocked on the API key from vendor\n- promised: revised beta date to ops by Friday\n- note: retro moved to Thursdays"
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
            {result.proposals.length === 0 ? (
              "No proposals - nothing in the notes matched a known line type."
            ) : (
              <>
                {created.length > 0 && (
                  <>
                    ✅ {created.length} proposal{created.length === 1 ? "" : "s"} created
                  </>
                )}
                {created.length > 0 && pending.length > 0 && " · "}
                {pending.length > 0 && <>{pending.length} already pending</>}
                {" - "}
                <Link href="/review" className="font-medium underline">
                  review them
                </Link>
              </>
            )}
            {result.skipped_private > 0 && (
              <span className="ml-2 text-weld">
                · {result.skipped_private} fb: line
                {result.skipped_private === 1 ? "" : "s"} skipped (private -
                use quick capture with your key)
              </span>
            )}
          </p>
          <ul className="space-y-1">
            {result.proposals.map((p, i) => (
              // the id repeats when one paste holds the same line twice
              <li key={`${p.id}-${i}`} className="text-ink-2">
                <span className="mr-2 rounded bg-raised px-1.5 py-0.5 text-xs">
                  {p.kind}
                </span>
                {p.line}
                {p.already_pending && (
                  <span className="ml-2 text-ink-3">already pending as #{p.id}</span>
                )}
              </li>
            ))}
          </ul>
          {result.unclassified.length > 0 && (
            <div>
              <p className="mb-1 font-medium text-ink-3">
                Not captured ({result.unclassified.length}) - file any that
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
              {result.unclassified.length > 20 && (
                <p className="mt-1 text-xs text-ink-3">
                  {result.unclassified.length - 20} more{" "}
                  {result.unclassified.length - 20 === 1 ? "line is" : "lines are"} not
                  shown. Paste a smaller batch to file them here, or capture them
                  with <Shortcut />.
                </p>
              )}
              {/* the heading above counts every line, the list renders 20:
                  without this the reader counts twenty rows under a heading
                  that says thirty-five and cannot tell which is wrong */}
            </div>
          )}
        </div>
      )}
    </main>
  );
}
