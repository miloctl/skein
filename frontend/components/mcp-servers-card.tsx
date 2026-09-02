"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";

import { Card as Section } from "@/components/card";
import { actionError, api } from "@/lib/api";
import { reportStatus } from "@/lib/status";

type ToolRow = { name: string; effect: string; risk: string };
type ServerStatus = {
  server_id: string;
  tier: string;
  connected: boolean;
  offered: number;
  retry_in_seconds: number | null;
  tools: ToolRow[];
};
type PersonalServer = {
  id: number;
  name: string;
  url: string;
  auth: "token" | "oauth";
  has_token: boolean;
  signed_in: boolean;
  sign_in_required?: boolean;
  server_id: string;
  status: ServerStatus | null;
};
type Payload = {
  sealing: boolean;
  system: ServerStatus[];
  personal: PersonalServer[];
};

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

function describe(status: ServerStatus | null | undefined): string {
  if (!status) return "not connected yet";
  if (status.connected)
    return `connected, ${status.tools.length} of ${plural(status.offered, "tool")} governed`;
  if (status.retry_in_seconds !== null)
    return `not connected, next attempt in ${status.retry_in_seconds} s`;
  return "not connected";
}

function ToolList({ tools, label }: { tools: ToolRow[]; label: string }) {
  if (tools.length === 0) return null;
  return (
    <ul aria-label={label} className="mt-1 flex flex-wrap gap-1.5">
      {tools.map((t) => (
        <li
          key={t.name}
          className="rounded-full bg-raised px-2 py-0.5 font-mono text-[10px] text-ink-3"
        >
          {t.name} · {t.effect}
        </li>
      ))}
    </ul>
  );
}

/** Remote MCP servers the agent can call. The system list is the operator's
 *  SKEIN_MCP_SERVERS (names and health only: a URL is deployment shape the
 *  API withholds). Personal rows are the reader's own, and the backend joins
 *  them only to the turns that reader drives (team_agent.build_agent). */
export function McpServersCard({
  strong,
  headingLevel = 2,
}: {
  strong: boolean;
  headingLevel?: 2 | 3;
}) {
  // null, never an empty payload: the page must not claim "no servers"
  // before the request answers
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [oauth, setOauth] = useState(false);
  const [deleting, setDeleting] = useState<number | null>(null);
  // a sign-in finishes in another tab: poll until the server connects, or
  // give up after the grant's own lifetime
  const [awaiting, setAwaiting] = useState<number | null>(null);
  const awaitingRef = useRef<number | null>(null);
  const [busy, setBusy] = useState("");
  const restoreTo = useRef<HTMLElement | null>(null);
  const introId = useId();
  const gen = useRef(0);

  const load = useCallback(() => {
    const mine = ++gen.current;
    api<Payload>("/api/mcp/servers")
      .then((p) => {
        if (mine !== gen.current) return;
        const personal: PersonalServer[] = Array.isArray(p?.personal) ? p.personal : [];
        setData({
          sealing: !!p?.sealing,
          system: Array.isArray(p?.system) ? p.system : [],
          personal,
        });
        setError("");
        const row = personal.find((s) => s.id === awaitingRef.current);
        if (row?.status?.connected) {
          awaitingRef.current = null;
          setAwaiting(null);
          reportStatus(`Server "${row.name}" signed in and connected.`, "confirmation");
        }
      })
      .catch((e) => {
        if (mine !== gen.current) return;
        setData(null);
        setError(`Cannot load remote MCP servers. ${actionError(e)}`);
      });
  }, []);
  useEffect(() => {
    if (strong) load();
  }, [load, strong]);
  useEffect(() => {
    if (awaiting === null) return;
    const timer = setInterval(load, 3000);
    const stop = setTimeout(() => {
      awaitingRef.current = null;
      setAwaiting(null);
    }, 5 * 60 * 1000);
    return () => {
      clearInterval(timer);
      clearTimeout(stop);
    };
  }, [awaiting, load]);

  const act = async (
    key: string,
    run: () => Promise<unknown>,
    done: (result: unknown) => string,
  ): Promise<boolean> => {
    if (busy) return false;
    // a delete click already recorded its own button: the autofocused
    // confirm button unmounts with the row, and focusing it lands on body
    restoreTo.current ??= document.activeElement as HTMLElement | null;
    setBusy(key);
    try {
      const result = await run();
      load();
      reportStatus(done(result), "confirmation");
      return true;
    } catch (e) {
      reportStatus(actionError(e));
      return false;
    } finally {
      setBusy("");
      requestAnimationFrame(() => {
        const el = restoreTo.current;
        restoreTo.current = null;
        if (el && document.contains(el)) el.focus();
      });
    }
  };

  const cancelDelete = (id: number) => {
    setDeleting(null);
    requestAnimationFrame(() =>
      document.getElementById(`delete-mcp-${id}`)?.focus(),
    );
  };

  const ItemHeading = headingLevel === 3 ? "h4" : "h3";

  return (
    <Section title="Remote MCP servers" headingLevel={headingLevel}>
      <p id={introId} className="mb-3 text-sm text-ink-3">
        A remote MCP server gives the agent more tools. Whoever runs the server
        configures the system-wide list. You can add your own servers. Your
        servers join only the chat turns you start. Skein classifies each tool
        from the server&apos;s own annotations: a read tool creates one review
        the first time it runs, and a write tool creates a review each time.
        To add a server, use strong identity.
      </p>

      <p role="status" className="text-sm text-danger empty:hidden">
        {error}
      </p>

      {!strong ? (
        <p className="text-sm text-ink-3">
          This card requires strong identity. If deployment sign-in is
          available, use it. Otherwise, use a personal API key.
        </p>
      ) : error ? null : data === null ? (
        <p className="text-sm text-ink-3">Loading…</p>
      ) : (
        <>
          <ItemHeading className="mt-2 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
            System-wide
          </ItemHeading>
          {data.system.length === 0 ? (
            <p className="mt-1 text-sm text-ink-3">
              No system-wide servers. Whoever runs the server sets
              SKEIN_MCP_SERVERS.
            </p>
          ) : (
            <ul aria-label="System-wide MCP servers" className="mt-1 space-y-2">
              {data.system.map((s) => (
                <li key={s.server_id} className="rounded-lg border border-line p-3 text-sm">
                  <span className="font-medium">{s.server_id}</span>
                  <span className="ml-2 font-mono text-[10px] text-ink-3">{describe(s)}</span>
                  <ToolList tools={s.tools} label={`Tools from ${s.server_id}`} />
                </li>
              ))}
            </ul>
          )}

          <ItemHeading className="mt-4 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
            Yours
          </ItemHeading>
          {data.personal.length === 0 ? (
            <p className="mt-1 text-sm text-ink-3">No personal servers yet.</p>
          ) : (
            <ul aria-label="Your MCP servers" aria-describedby={introId} className="mt-1 space-y-2">
              {data.personal.map((s) => (
                <li
                  key={s.id}
                  onKeyDown={(e) => {
                    if (e.key !== "Escape" || deleting !== s.id) return;
                    e.stopPropagation();
                    cancelDelete(s.id);
                  }}
                  className="rounded-lg border border-line p-3 text-sm"
                >
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span>
                      <span className="font-medium">{s.name}</span>
                      <span className="ml-2 font-mono text-[10px] text-ink-3">
                        {s.auth === "oauth" && s.sign_in_required
                          ? "sign-in needed"
                          : describe(s.status)}
                        {s.has_token ? " · token stored" : ""}
                        {s.auth === "oauth" && s.signed_in ? " · signed in" : ""}
                      </span>
                      {s.auth === "oauth" && (
                        <button
                          disabled={!!busy || awaiting === s.id}
                          onClick={async () => {
                            const ok = await act(
                              `s${s.id}`,
                              async () => {
                                const reply = await api<{ authorization_url: string }>(
                                  `/api/mcp/servers/${s.id}/sign-in`,
                                  { method: "POST" },
                                );
                                window.open(reply.authorization_url, "_blank", "noopener");
                              },
                              () => `Sign-in opened in a new tab for "${s.name}".`,
                            );
                            if (ok) {
                              awaitingRef.current = s.id;
                              setAwaiting(s.id);
                            }
                          }}
                          className="ml-2 rounded-lg border border-line-strong px-2 py-0.5 text-xs hover:border-thread-solid disabled:opacity-50"
                        >
                          {awaiting === s.id ? "waiting for sign-in…" : "Sign in"}
                        </button>
                      )}
                    </span>
                    {deleting === s.id ? (
                      <span className="flex items-center gap-1.5 text-xs">
                        <span id={`delete-mcp-${s.id}-consequence`} className="text-[10px] text-ink-3">
                          After deletion, your chats lose these tools and the
                          stored token is deleted.
                        </span>
                        <button
                          autoFocus
                          aria-label={`Confirm: delete server ${s.name}`}
                          aria-describedby={`delete-mcp-${s.id}-consequence`}
                          disabled={!!busy}
                          onClick={async () => {
                            const ok = await act(
                              `d${s.id}`,
                              () => api(`/api/mcp/servers/${s.id}`, { method: "DELETE" }),
                              () => `Server "${s.name}" deleted.`,
                            );
                            if (ok) setDeleting(null);
                          }}
                          className="rounded bg-danger-solid px-2 py-0.5 font-medium text-white hover:opacity-90 disabled:opacity-50"
                        >
                          delete
                        </button>
                        <button
                          aria-label="Cancel deletion"
                          onClick={() => cancelDelete(s.id)}
                          className="min-h-6 px-1 text-ink-3 hover:text-ink"
                        >
                          cancel
                        </button>
                      </span>
                    ) : (
                      <button
                        id={`delete-mcp-${s.id}`}
                        aria-label={`Delete server ${s.name}`}
                        onClick={(e) => {
                          restoreTo.current = e.currentTarget;
                          setDeleting(s.id);
                        }}
                        className="min-h-6 text-xs text-ink-3 hover:text-ink"
                      >
                        delete…
                      </button>
                    )}
                  </div>
                  <code className="mt-1 block break-all text-xs text-ink-3">{s.url}</code>
                  <ToolList tools={s.status?.tools ?? []} label={`Tools from ${s.name}`} />
                </li>
              ))}
            </ul>
          )}

          <form
            className="mt-3 flex flex-wrap items-center gap-2"
            onSubmit={async (e) => {
              e.preventDefault();
              const ok = await act(
                "new",
                () =>
                  api<PersonalServer>("/api/mcp/servers", {
                    method: "POST",
                    body: JSON.stringify({
                      name: name.trim(),
                      url: url.trim(),
                      auth_token: oauth ? "" : token,
                      auth: oauth ? "oauth" : "token",
                    }),
                  }),
                (result) =>
                  `Server "${name.trim()}" added: ${describe((result as PersonalServer)?.status)}.`,
              );
              if (ok) {
                setName("");
                setUrl("");
                setToken("");
                setOauth(false);
              }
            }}
          >
            <input
              name="mcp-name"
              aria-label="Server name"
              placeholder="name (a-z, 0-9, - or _)"
              value={name}
              maxLength={40}
              pattern="[a-z0-9][a-z0-9_\-]{0,39}"
              onChange={(e) => setName(e.target.value)}
              className="w-40 rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
            />
            <input
              name="mcp-url"
              type="url"
              aria-label="Server URL"
              placeholder="https://host/mcp"
              value={url}
              maxLength={2000}
              onChange={(e) => setUrl(e.target.value)}
              className="w-72 rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
            />
            <input
              name="mcp-token"
              type="password"
              autoComplete="off"
              aria-label="Bearer token (optional)"
              placeholder={data.sealing ? "token (optional)" : "token storage is off"}
              value={token}
              maxLength={4000}
              disabled={!data.sealing || oauth}
              onChange={(e) => setToken(e.target.value)}
              className="w-56 rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid disabled:opacity-50"
            />
            <label className="flex items-center gap-1 text-xs text-ink-3">
              <input
                type="checkbox"
                checked={oauth}
                disabled={!data.sealing}
                onChange={(e) => setOauth(e.target.checked)}
              />
              sign in with OAuth
            </label>
            <button
              type="submit"
              disabled={!!busy || !name.trim() || !url.trim()}
              className="rounded-lg bg-thread-solid px-2.5 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              Add server
            </button>
          </form>
          {!data.sealing && (
            <p className="mt-2 text-xs text-ink-3">
              A token cannot be stored: SKEIN_CREDENTIAL_KEY is not set. Whoever
              runs the server must set it. A server without a token can be added.
            </p>
          )}
          {data.sealing && (
            <p className="mt-2 text-xs text-ink-3">
              If the server uses OAuth, select sign in with OAuth, add the server,
              then select Sign in on its row. The sign-in opens in a new tab.
            </p>
          )}
        </>
      )}
    </Section>
  );
}
