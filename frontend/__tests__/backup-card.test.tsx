import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** The backup card's two claims that must stay true: only a strong admin
 *  gets buttons (anyone else gets the refusal line, never a button that
 *  answers 403), and the export path saves a FILE — the api result must
 *  reach a download, not just the status line. */

const calls: { path: string; method: string }[] = [];
const mode: { backup: "ok" | "fail" } = { backup: "ok" };

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: RequestInit) => {
      calls.push({ path, method: init?.method ?? "GET" });
      if (path === "/api/admin/backup") {
        return mode.backup === "ok"
          ? Promise.resolve({
              path: "/data/backups/platform-2026-08-14.db",
              private_path: null,
              kept: 3,
              mirrored: "/backup-mirror/platform-2026-08-14.db",
            })
          : Promise.reject(new Error("backup service exploded"));
      }
      return Promise.resolve({ tasks: [] });
    },
  };
});

import { BackupCard } from "@/components/backup-card";

describe("BackupCard", () => {
  it("shows the refusal line and no buttons without administrator access", () => {
    render(<BackupCard
        canAdminister={false}
        accessMessage="You do not have administrator access. Ask an administrator for access."
      />);
    expect(
      screen.getByText(
        "You do not have administrator access. Ask an administrator for access.",
      ),
    ).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("shows identity checking instead of a weak-identity verdict while loading", () => {
    render(
      <BackupCard canAdminister={false} accessMessage="Checking identity…" />,
    );
    expect(screen.getByText("Checking identity…")).toBeTruthy();
    expect(screen.queryByText(/requires strong identity/)).toBeNull();
  });

  it("states both requirements for weak identity", () => {
    render(
      <BackupCard
        canAdminister={false}
        accessMessage="This action requires strong identity and administrator access. If deployment sign-in is available, use it. Otherwise, use a personal API key. If the action is still unavailable, ask an administrator for access."
      />,
    );
    expect(
      screen.getByText(
        "This action requires strong identity and administrator access. If deployment sign-in is available, use it. Otherwise, use a personal API key. If the action is still unavailable, ask an administrator for access.",
      ),
    ).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("backs up on demand and names the file and the mirror", async () => {
    calls.length = 0;
    render(<BackupCard canAdminister={true} accessMessage="" />);
    fireEvent.click(screen.getByRole("button", { name: "Back up now" }));
    expect(
      await screen.findByText("Backup complete: platform-2026-08-14.db, mirrored off-box."),
    ).toBeTruthy();
    expect(calls).toContainEqual({ path: "/api/admin/backup", method: "POST" });
  });

  it("saves the export as a dated file", async () => {
    const clicked: string[] = [];
    const realCreate = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = realCreate(tag);
      if (tag === "a") {
        el.click = () => clicked.push((el as HTMLAnchorElement).download);
      }
      return el;
    });
    URL.createObjectURL = vi.fn(() => "blob:test");
    URL.revokeObjectURL = vi.fn();

    render(<BackupCard canAdminister={true} accessMessage="" />);
    fireEvent.click(screen.getByRole("button", { name: "Download export" }));
    await screen.findByText(/Export saved as skein-export-/);
    expect(clicked).toHaveLength(1);
    expect(clicked[0]).toMatch(/^skein-export-\d{4}-\d{2}-\d{2}\.json$/);
    vi.restoreAllMocks();
  });

  it("a failed backup reports the fault, not a fake success", async () => {
    mode.backup = "fail";
    render(<BackupCard canAdminister={true} accessMessage="" />);
    fireEvent.click(screen.getByRole("button", { name: "Back up now" }));
    expect(
      await screen.findByText(/backup service exploded/),
    ).toBeTruthy();
    expect(screen.queryByText(/Backup complete/)).toBeNull();
  });
});
