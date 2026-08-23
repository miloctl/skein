import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const calls: { path: string; method: string }[] = [];
const mode: {
  backup: "written" | "not_configured" | "unavailable" | "fail";
} = { backup: "written" };
const fetchMock = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    authenticatedFetch: (path: string, init?: RequestInit) =>
      fetchMock(path, init),
    api: (path: string, init?: RequestInit) => {
      calls.push({ path, method: init?.method ?? "GET" });
      if (path === "/api/admin/backup") {
        if (mode.backup === "fail") {
          return Promise.reject(new Error("backup service exploded"));
        }
        return Promise.resolve({
          status: mode.backup === "unavailable" ? "partial" : "ok",
          database_path:
            "/data/backups/database-2026-08-14-120000-abcd1234.dump",
          kept: 3,
          mirror_status: mode.backup,
          mirrored_platform_path:
            mode.backup === "written"
              ? "/backup-mirror/platform-2026-08-14.dump"
              : null,
          artifacts_included: false,
        });
      }
      return Promise.reject(new Error(`unexpected api call: ${path}`));
    },
  };
});

import { BackupCard } from "@/components/backup-card";

beforeEach(() => {
  calls.length = 0;
  mode.backup = "written";
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("BackupCard", () => {
  it("shows the refusal line and no buttons without administrator access", () => {
    render(
      <BackupCard
        canAdminister={false}
        accessMessage="You do not have administrator access. Ask an administrator for access."
      />,
    );
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

  it("states that the configured mirror received only the platform dump", async () => {
    render(<BackupCard canAdminister={true} accessMessage="" />);
    fireEvent.click(screen.getByRole("button", { name: "Back up now" }));
    expect(
      await screen.findByText(
        "Database backup complete locally: database-2026-08-14-120000-abcd1234.dump. The core-schema dump was copied to the configured mirror. Protect that mirror like the database. Artifact files are not included.",
      ),
    ).toBeTruthy();
    expect(calls).toContainEqual({ path: "/api/admin/backup", method: "POST" });
  });

  it("states when no mirror is configured", async () => {
    mode.backup = "not_configured";
    render(<BackupCard canAdminister={true} accessMessage="" />);
    fireEvent.click(screen.getByRole("button", { name: "Back up now" }));
    expect(
      await screen.findByText(
        "Database backup complete locally: database-2026-08-14-120000-abcd1234.dump. No backup mirror is configured. Artifact files are not included.",
      ),
    ).toBeTruthy();
  });

  it("reports a configured mirror failure without hiding local success", async () => {
    mode.backup = "unavailable";
    render(<BackupCard canAdminister={true} accessMessage="" />);
    fireEvent.click(screen.getByRole("button", { name: "Back up now" }));
    expect(
      await screen.findByText(
        "The local database backup completed: database-2026-08-14-120000-abcd1234.dump. The configured mirror copy failed. Check the mirror mount and file permissions. Artifact files are not included.",
      ),
    ).toBeTruthy();
    expect(screen.queryByText(/Database backup complete locally/)).toBeNull();
  });

  it("downloads the authenticated response body without JSON reserialization", async () => {
    const clicked: string[] = [];
    const realCreate = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const element = realCreate(tag);
      if (tag === "a") {
        element.click = () =>
          clicked.push((element as HTMLAnchorElement).download);
      }
      return element;
    });
    URL.createObjectURL = vi.fn(() => "blob:test");
    URL.revokeObjectURL = vi.fn();
    fetchMock.mockResolvedValue(
      new Response('{"tasks":[{"id":9007199254740993}]}', {
        status: 200,
        headers: {
          "Content-Type": "application/json",
          "X-Skein-Filename": "skein-export-2026-08-21.json",
        },
      }),
    );

    render(<BackupCard canAdminister={true} accessMessage="" />);
    fireEvent.click(screen.getByRole("button", { name: "Download export" }));
    await screen.findByText(/Export saved as skein-export-/);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/export/download",
      undefined,
    );
    expect(calls).not.toContainEqual({
      path: "/api/admin/export",
      method: "GET",
    });
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    const blob = vi.mocked(URL.createObjectURL).mock.calls[0][0] as Blob;
    expect(await blob.text()).toBe('{"tasks":[{"id":9007199254740993}]}');
    expect(clicked).toEqual(["skein-export-2026-08-21.json"]);
  });

  it("does not download an error response", async () => {
    const clicked: string[] = [];
    const realCreate = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const element = realCreate(tag);
      if (tag === "a") element.click = () => clicked.push("clicked");
      return element;
    });
    URL.createObjectURL = vi.fn(() => "blob:test");
    URL.revokeObjectURL = vi.fn();
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({ detail: "The export failed. Check the server log." }),
        {
          status: 500,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    render(<BackupCard canAdminister={true} accessMessage="" />);
    fireEvent.click(screen.getByRole("button", { name: "Download export" }));
    expect(
      await screen.findByText("The export failed. Check the server log."),
    ).toBeTruthy();
    expect(clicked).toEqual([]);
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(screen.queryByText(/Export saved/)).toBeNull();
  });

  it("a failed backup reports the fault, not a fake success", async () => {
    mode.backup = "fail";
    render(<BackupCard canAdminister={true} accessMessage="" />);
    fireEvent.click(screen.getByRole("button", { name: "Back up now" }));
    expect(await screen.findByText(/backup service exploded/)).toBeTruthy();
    expect(screen.queryByText(/Backup complete/)).toBeNull();
  });
});
