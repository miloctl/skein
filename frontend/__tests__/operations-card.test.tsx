import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

type Job = {
  job: string;
  last_success: string | null;
  last_attempt: string | null;
  last_status: "ok" | "error" | null;
  stale: boolean;
};

const healthy = {
  auth_error: "",
  auth_warnings: [],
  provider_error: "",
  models_error: "",
  model_prices_error: "",
  model_warnings: [],
  embeddings_error: "",
  overlay_errors: [],
  database_warnings: [],
  identity_ownership_error: "",
  context_error: "",
  timezone: "UTC",
  timezone_error: "",
  jobs: [] as Job[],
  activity_chain: {
    verified_through: 2,
    latest: 3,
    unverified: 1,
    high_water: 3,
    marks_ok: true,
  },
};
let response = structuredClone(healthy);
// null: the request fails, as it does for anyone but a named administrator
let stranded: unknown = null;

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) =>
      path === "/api/review/stranded"
        ? stranded === null
          ? Promise.reject(new Error("forbidden"))
          : Promise.resolve(stranded)
        : Promise.resolve(response),
  };
});

import { OperationsCard } from "@/components/operations-card";

describe("OperationsCard", () => {
  beforeEach(() => {
    response = structuredClone(healthy);
    stranded = null;
  });

  it("lists the proposals nobody can settle to a named administrator", async () => {
    stranded = [
      { id: 12, entity: "note", action: "create", reason: "No active person can read it." },
    ];
    render(<OperationsCard />);
    expect(
      await screen.findByText(/#12 note\.create: No active person can read it\./),
    ).toBeTruthy();
    expect(screen.getByText(/No one can approve or reject this pending proposal/)).toBeTruthy();
    expect(screen.queryByText(/The loom hums/)).toBeNull();
  });

  it("stays all clear when the stranded list is not the reader's to see", async () => {
    render(<OperationsCard />);
    expect(await screen.findByText(/The loom hums/)).toBeTruthy();
  });

  it("reports a chain tip that contradicts the integrity marks", async () => {
    response.activity_chain.marks_ok = false;
    render(<OperationsCard />);
    expect(
      await screen.findByText(
        /stored activity ledger does not match its integrity marks/i,
      ),
    ).toBeTruthy();
    expect(screen.queryByText(/The loom hums/)).toBeNull();
  });

  it("reports the latest failed job before it becomes stale", async () => {
    response.jobs = [
      {
        job: "activity-verify",
        last_success: "2026-08-22T01:00:00+00:00",
        last_attempt: "2026-08-22T02:00:00+00:00",
        last_status: "error",
        stale: false,
      },
    ];
    render(<OperationsCard />);
    expect(await screen.findByText(/failed — last attempt/i)).toBeTruthy();
    expect(screen.queryByText(/The loom hums/)).toBeNull();
  });

  it("refuses a health response with a missing warning array", async () => {
    response.database_warnings = undefined as never;
    render(<OperationsCard />);
    expect(
      await screen.findByText(/health response has an unexpected shape/i),
    ).toBeTruthy();
    expect(screen.queryByText(/The loom hums/)).toBeNull();
  });

  it.each(["last_status", "last_attempt"])(
    "refuses a job without %s",
    async (field) => {
      response.jobs = [
        {
          job: "activity-verify",
          last_success: null,
          last_attempt: null,
          last_status: null,
          stale: false,
        },
      ];
      delete (response.jobs[0] as unknown as Record<string, unknown>)[field];
      render(<OperationsCard />);
      expect(
        await screen.findByText(/health response has an unexpected shape/i),
      ).toBeTruthy();
      expect(screen.queryByText(/The loom hums/)).toBeNull();
    },
  );

  it("refuses a health response without the required ledger status", async () => {
    response.activity_chain.marks_ok = undefined as never;
    render(<OperationsCard />);
    expect(
      await screen.findByText(/health response has an unexpected shape/i),
    ).toBeTruthy();
    expect(screen.queryByText(/integrity marks/i)).toBeNull();
  });
});
