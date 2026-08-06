"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";

import { Card as Section } from "@/components/card";
import { PersonInput } from "@/components/person-input";
import { actionError, api } from "@/lib/api";
import { reportStatus } from "@/lib/status";

type CrewMember = { person: string; role: "member" | "steward" };
export type Crew = {
  id: number;
  name: string;
  summary: string;
  active: number;
  members: CrewMember[];
};

/** Crew membership. A crew grants nothing yet — the visibility tier that reads
 *  it is docs/VISIBILITY.md phase 3 — so this card says what it is FOR without
 *  claiming it does anything today.
 *
 *  `me` is the SERVER-resolved identity from /api/whoami, never getUser(): a
 *  personal API key wins over the X-User display name in every auth mode
 *  (routes/deps.py), so a steward whose two names differ would see no controls
 *  at all, and a non-steward whose display name matched one would see buttons
 *  that only ever answer 403. */
export function CrewsCard({
  strong,
  me,
  admin,
}: {
  strong: boolean;
  me: string;
  admin: boolean;
}) {
  // null, never []: an empty array is a claim that there are no crews, and
  // the page must not make it before the request answers
  const [crews, setCrews] = useState<Crew[] | null>(null);
  const [error, setError] = useState("");
  const [newName, setNewName] = useState("");
  const [removing, setRemoving] = useState<string | null>(null);
  // keyed per crew: one shared flag disabled the Add and deactivate buttons of
  // every OTHER crew mid-write, pulling them out of the tab order
  const [busy, setBusy] = useState("");
  // Chrome blurs a focused element the moment it is disabled, so every write
  // dropped focus to document.body — on the very control the reader pressed.
  // The refused case was worst: the confirm stayed open and unreachable.
  const restoreTo = useRef<HTMLElement | null>(null);
  const introId = useId();
  // last-request-wins. act() fires load() without awaiting, so two quick
  // writes overlap two GETs: a slow earlier response lands last and reverts
  // the list, and a slow earlier REJECTION wipes a list that loaded fine.
  const gen = useRef(0);

  const load = useCallback(() => {
    const mine = ++gen.current;
    api<Crew[]>("/api/crews?all=1")
      .then((c) => {
        if (mine !== gen.current) return;
        setCrews(c);
        setError("");
      })
      .catch((e) => {
        if (mine !== gen.current) return;
        setCrews(null);
        // names the card, not the page: one failed card on /settings must not
        // report that the whole page could not load
        setError(`Cannot load crews. ${actionError(e)}`);
      });
  }, []);
  useEffect(load, [load]);

  /** Returns whether the write landed. Callers reset their inputs only on
   *  true — a catch that resolves would clear what the reader typed at the
   *  exact moment they need it back. */
  const act = async (
    key: string,
    run: () => Promise<unknown>,
    done: string,
  ): Promise<boolean> => {
    if (busy) return false;
    restoreTo.current = document.activeElement as HTMLElement | null;
    setBusy(key);
    try {
      await run();
      load();
      reportStatus(done, "confirmation");
      return true;
    } catch (e) {
      reportStatus(actionError(e));
      return false;
    } finally {
      setBusy("");
      // after the re-render, not inside it: the control may have been
      // unmounted (a removed chip) or merely re-enabled
      requestAnimationFrame(() => {
        const el = restoreTo.current;
        if (el && document.contains(el)) el.focus();
      });
    }
  };

  // the server accepts a steward of THIS crew or an administrator
  // (routes/api.py::_crew_steward). Offering less strands a crew whose only
  // steward left; offering more is a button that answers 403.
  const canEdit = (crew: Crew) =>
    strong &&
    (admin ||
      crew.members.some((m) => m.person === me && m.role === "steward"));

  return (
    <Section title="Crews">
      <p id={introId} className="mb-3 text-sm text-ink-3">
        A crew is a durable group of people. Today it is a list of names — it
        changes nothing about what anyone can see. Whoever makes a crew becomes
        its steward, and a steward adds and removes members. Requires a working
        API key (step 2), and stewardship of the crew or administrator access.
      </p>

      {strong && (
        <form
          className="mb-4 flex flex-wrap items-center gap-2"
          onSubmit={async (e) => {
            e.preventDefault();
            const ok = await act(
              "new",
              () =>
                api("/api/crews", {
                  method: "POST",
                  body: JSON.stringify({ name: newName.trim() }),
                }),
              `Crew "${newName.trim()}" created.`,
            );
            if (ok) setNewName("");
          }}
        >
          <input
            name="crew-name"
            aria-label="New crew name"
            placeholder="new crew name"
            value={newName}
            maxLength={60}
            onChange={(e) => setNewName(e.target.value)}
            className="w-56 rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
          />
          <button
            type="submit"
            disabled={!!busy || !newName.trim()}
            className="rounded-lg bg-thread-solid px-2.5 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            Create crew
          </button>
        </form>
      )}

      {/* role=status on a node that is ALWAYS mounted: a live region inserted
          in the same paint as its text is not announced (the pattern
          app/settings/page.tsx documents). act() fires load() without
          awaiting, so a failed reload wipes the list one tick after the
          confirmation says the write landed. */}
      <p role="status" className="text-sm text-danger empty:hidden">
        {error}
      </p>
      {error ? null : crews === null ? (
        <p className="text-sm text-ink-3">Loading…</p>
      ) : crews.length === 0 ? (
        <p className="text-sm text-ink-3">No crews yet.</p>
      ) : (
        <ul aria-describedby={introId} className="space-y-3">
          {crews.map((crew) => (
            <li
              key={crew.id}
              className={
                "rounded-lg border p-3 " +
                // NOT opacity: dimming the container composites every token at
                // 60% AFTER the theme system has done its work, and measured
                // 2.3:1 to 3.1:1 in every pack — including `contrast`, the
                // high-contrast one. A deactivated crew keeps working
                // controls, so the 1.4.3 inactive-component exception does
                // not apply.
                (crew.active
                  ? "border-line"
                  : "border-dashed border-line-strong bg-raised")
              }
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                {/* the badge sits OUTSIDE the heading: inside, the accessible
                    name of the heading becomes "Platform inactive" — the
                    badge is a state, not part of what the crew is called */}
                <h3 className="font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
                  {crew.name}
                </h3>
                <span className="font-mono text-[10px] text-ink-3">
                  {/* a space, not only a margin: an accname computation reads
                      these as one run and announced "inactive1 member" */}
                  {!crew.active && <span className="mr-1.5">inactive </span>}
                  {crew.members.length} member
                  {crew.members.length === 1 ? "" : "s"}
                </span>
              </div>
              {crew.summary && (
                <p className="mt-1 text-xs text-ink-3">{crew.summary}</p>
              )}

              <ul
                aria-label={`Members of ${crew.name}`}
                className="mt-2 flex flex-wrap gap-1.5"
              >
                {crew.members.map((m) => (
                  <li
                    key={m.person}
                    className="flex items-center gap-1 rounded-full bg-raised px-2 py-0.5 text-xs"
                  >
                    <span>{m.person}</span>
                    {m.role === "steward" && (
                      <span className="font-mono text-[10px] text-ink-3">
                        steward
                      </span>
                    )}
                    {canEdit(crew) &&
                      (removing === `${crew.id}:${m.person}` ? (
                        <>
                          <button
                            autoFocus
                            aria-label={`Confirm: remove ${m.person} from ${crew.name}`}
                            disabled={!!busy}
                            onClick={async () => {
                              const ok = await act(
                                `c${crew.id}`,
                                () =>
                                  api(`/api/crews/${crew.id}/members/remove`, {
                                    method: "POST",
                                    body: JSON.stringify({ person: m.person }),
                                  }),
                                `${m.person} removed from ${crew.name}.`,
                              );
                              if (ok) setRemoving(null);
                            }}
                            className="rounded bg-danger-solid px-2 py-0.5 font-medium text-white hover:opacity-90 disabled:opacity-50"
                          >
                            remove
                          </button>
                          <button
                            aria-label={`Keep ${m.person} in ${crew.name}`}
                            onClick={() => setRemoving(null)}
                            className="min-h-6 px-1 text-ink-3 hover:text-ink"
                          >
                            keep
                          </button>
                        </>
                      ) : (
                        <button
                          aria-label={`Remove ${m.person} from ${crew.name}`}
                          onClick={(e) => {
                            restoreTo.current = e.currentTarget;
                            setRemoving(`${crew.id}:${m.person}`);
                          }}
                          className="min-h-6 min-w-6 text-ink-3 hover:text-ink"
                        >
                          ×
                        </button>
                      ))}
                  </li>
                ))}
              </ul>

              {canEdit(crew) && (
                <form
                  className="mt-2 flex flex-wrap items-center gap-1.5"
                  onSubmit={async (e) => {
                    e.preventDefault();
                    const form = e.currentTarget;
                    const person = (
                      form.elements.namedItem("person") as HTMLInputElement
                    ).value.trim();
                    const role = (
                      form.elements.namedItem("role") as HTMLSelectElement
                    ).value;
                    if (!person) return;
                    const ok = await act(
                      `c${crew.id}`,
                      () =>
                        api(`/api/crews/${crew.id}/members`, {
                          method: "POST",
                          body: JSON.stringify({ person, role }),
                        }),
                      `${person} added to ${crew.name}.`,
                    );
                    if (ok) form.reset();
                  }}
                >
                  <PersonInput
                    name="person"
                    aria-label={`Add someone to ${crew.name}`}
                    placeholder="add a teammate"
                    maxLength={64}
                    className="w-44 rounded-lg border border-line-strong bg-transparent px-2 py-0.5 text-xs outline-none focus:border-thread-solid"
                  />
                  <select
                    name="role"
                    aria-label={`Role in ${crew.name}`}
                    defaultValue="member"
                    className="rounded-lg border border-line-strong bg-transparent px-2 py-0.5 text-xs outline-none focus:border-thread-solid"
                  >
                    <option value="member">member</option>
                    <option value="steward">steward</option>
                  </select>
                  <button
                    type="submit"
                    aria-label={`Add to ${crew.name}`}
                    disabled={!!busy}
                    className="rounded-lg bg-thread-solid px-2 py-0.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
                  >
                    Add
                  </button>
                  <button
                    type="button"
                    disabled={!!busy}
                    onClick={() =>
                      act(
                        `c${crew.id}`,
                        () =>
                          api(`/api/crews/${crew.id}`, {
                            method: "PATCH",
                            body: JSON.stringify({ active: !crew.active }),
                          }),
                        crew.active
                          ? `${crew.name} deactivated.`
                          : `${crew.name} reactivated.`,
                      )
                    }
                    aria-label={
                      crew.active
                        ? `Deactivate ${crew.name}`
                        : `Reactivate ${crew.name}`
                    }
                    className="ml-auto rounded-lg border border-line-strong px-2 py-0.5 text-xs hover:bg-raised disabled:opacity-50"
                  >
                    {crew.active ? "deactivate" : "reactivate"}
                  </button>
                </form>
              )}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}
