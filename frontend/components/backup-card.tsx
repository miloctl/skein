"use client";

import { useState } from "react";

import { Card as Section } from "@/components/card";
import { actionError, api } from "@/lib/api";
import { reportStatus } from "@/lib/status";

type BackupResult = {
  path: string;
  private_path: string | null;
  kept: number;
  mirrored: string | null;
};

/** Manual backup and export, for administrators. The export is deliberately
 *  NOT the complete copy — services/admin.py::export excludes chat tables,
 *  private-visibility rows and the private schema — so the copy below must say which
 *  half is which, or an operator exports-then-deletes and loses every chat
 *  (the deploy/k8s/OPERATOR.md exit section makes the same distinction). */
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
      setLine(actionError(e));
    } finally {
      setBusy("");
    }
  };

  const backupNow = () =>
    run("backup", async () => {
      const out = await api<BackupResult>("/api/admin/backup", {
        method: "POST",
      });
      const name = out.path.split("/").pop() ?? out.path;
      return out.mirrored
        ? `Backup complete: ${name}, mirrored off-box.`
        : `Backup complete: ${name}.`;
    });

  const downloadExport = () =>
    run("export", async () => {
      const dump = await api<Record<string, unknown>>("/api/admin/export");
      const name = `skein-export-${new Date().toISOString().slice(0, 10)}.json`;
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(dump, null, 2)], { type: "application/json" }),
      );
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      a.click();
      URL.revokeObjectURL(url);
      return `Export saved as ${name}.`;
    });

  return (
    <Section title="Backups (team)" headingLevel={headingLevel}>
      <p className="mb-3 text-sm text-ink-3">
        Backups run daily on their own. Before a risky change (an upgrade, a
        bulk edit), an administrator can take one now. The export returns the
        shared work tables as JSON — it excludes chat transcripts and private
        notes. The backup files are the complete copy.
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
