"use client";

import { useState } from "react";

import { Card as Section } from "@/components/card";
import {
  actionError,
  api,
  authenticatedFetch,
  errorFromResponse,
} from "@/lib/api";
import { reportStatus } from "@/lib/status";

type BackupResult = {
  status: "ok" | "partial";
  database_path: string;
  kept: number;
  mirror_status: "not_configured" | "written" | "unavailable";
  mirrored_platform_path: string | null;
  artifacts_included: false;
};

/** Manual database backup and portable work export for administrators.
 *
 *  The export omits private and operational stores. The copy must keep it
 *  distinct from database recovery and from the artifact volume. */
export function BackupCard({
  canAdminister,
  accessMessage,
  headingLevel = 2,
}: {
  canAdminister: boolean;
  accessMessage: string;
  headingLevel?: 2 | 3;
}) {
  const [busy, setBusy] = useState("");
  const [line, setLine] = useState("");

  const run = async (key: string, fn: () => Promise<string>) => {
    if (busy) return;
    setBusy(key);
    try {
      const done = await fn();
      setLine(done);
      reportStatus(done, "confirmation");
    } catch (e) {
      const failed = actionError(e);
      setLine(failed);
      reportStatus(failed);
    } finally {
      setBusy("");
    }
  };

  const backupNow = () =>
    run("backup", async () => {
      const out = await api<BackupResult>("/api/admin/backup", {
        method: "POST",
      });
      const name = out.database_path.split("/").pop() ?? out.database_path;
      if (out.status === "partial" || out.mirror_status === "unavailable") {
        throw new Error(
          `The local database backup completed: ${name}. The configured mirror copy failed. Check the mirror mount and file permissions. Artifact files are not included.`,
        );
      }
      if (out.mirror_status === "written") {
        return `Database backup complete locally: ${name}. The core-schema dump was copied to the configured mirror. Protect that mirror like the database. Artifact files are not included.`;
      }
      return `Database backup complete locally: ${name}. No backup mirror is configured. Artifact files are not included.`;
    });

  const downloadExport = () =>
    run("export", async () => {
      const response = await authenticatedFetch("/api/admin/export/download");
      if (!response.ok) throw await errorFromResponse(response);
      const servedName = response.headers.get("X-Skein-Filename") ?? "";
      const name = /^skein-export-\d{4}-\d{2}-\d{2}\.json$/.test(servedName)
        ? servedName
        : `skein-export-${new Date().toISOString().slice(0, 10)}.json`;
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      link.click();
      URL.revokeObjectURL(url);
      return `Export saved as ${name}.`;
    });

  return (
    <Section title="Backups (team)" headingLevel={headingLevel}>
      <p className="mb-3 text-sm text-ink-3">
        Backups run daily. Before a risky change (an upgrade or bulk edit), an
        administrator can take one now. The JSON export contains workspace and
        crew work from one database snapshot. It excludes chats, private rows,
        review proposals, notifications, feedback, generated insights, the
        activity ledger, usage telemetry, context packs, deployment state, and
        artifact bytes. Use the database dumps for database recovery. Back up
        the artifact volume separately.
      </p>
      {canAdminister ? (
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={backupNow}
            disabled={!!busy}
            className="rounded border border-edge px-3 py-1.5 text-sm hover:bg-raised disabled:opacity-50"
          >
            {busy === "backup" ? "Backing up…" : "Back up now"}
          </button>
          <button
            type="button"
            onClick={downloadExport}
            disabled={!!busy}
            className="rounded border border-edge px-3 py-1.5 text-sm hover:bg-raised disabled:opacity-50"
          >
            {busy === "export" ? "Exporting…" : "Download export"}
          </button>
        </div>
      ) : (
        <p role="status" className="text-sm text-ink-3">
          {accessMessage}
        </p>
      )}
      {/* always-mounted status node, same reason as the tunables section:
          a live region inserted with its own text is not announced */}
      <p aria-live="polite" className="mt-3 min-h-4 text-sm text-ink-2">
        {line}
      </p>
    </Section>
  );
}
