"use client";

import { useEffect, useId, useState } from "react";

import { api } from "@/lib/api";

/** Text input with a datalist of existing teammates: picking a known name is
 *  one keystroke + Enter, but free-form stays allowed — new names are how
 *  people join the roster on a trusted network. Agents are excluded; leads and
 *  assignees are humans. */
export function PersonInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  const [people, setPeople] = useState<string[]>([]);
  const listId = useId();
  useEffect(() => {
    api<{ name: string; kind: string }[]>("/api/users")
      .then((u) => setPeople(u.filter((x) => x.kind !== "agent").map((x) => x.name)))
      .catch(() => {});
  }, []);
  return (
    <>
      <input {...props} list={listId} />
      <datalist id={listId}>
        {people.map((n) => (
          <option key={n} value={n} />
        ))}
      </datalist>
    </>
  );
}
