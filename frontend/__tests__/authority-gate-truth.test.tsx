import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** The Agents page answers one question: can an agent write without asking?
 *  The answer INVERTS with the review gate, which ships off by default
 *  (config.py SKEIN_AGENT_REVIEW). tools/_gate.py takes the direct path on
 *  `not config.AGENT_REVIEW` whatever the authority level, so with the gate
 *  off autonomous, notify and review all mean "acts alone" and only
 *  forbidden stops a write. The page used to state the gate-on rule
 *  unconditionally — a false reassurance about a safety control, in the
 *  configuration that is the default. */

const gate: { on: boolean | "never"; granted: boolean; entitiesFail: boolean } =
  {
    on: false,
    granted: true,
    entitiesFail: false,
  };

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => {
      if (path === "/api/agents/status") {
        // "never" holds the fetch open: the page must claim NEITHER rule
        // while the gate state is unknown
        if (gate.on === "never") return new Promise(() => {});
        return Promise.resolve({
          provider: "mock",
          model: "m",
          provider_error: "",
          review_gate: gate.on,
          context_strategy: "sliding",
          context_error: "",
        });
      }
      // Settings reads several endpoints and calls .trim()/.map() on their
      // fields — [] would crash the render before section 4 ever appears
      if (path === "/api/users/growth-interests")
        return Promise.resolve({ interests: "" });
      if (path === "/api/whoami")
        return Promise.resolve({
          user: "tester",
          strong: false,
          mode: "trusted-header",
        });
      if (path === "/api/settings/context-strategy")
        return Promise.resolve({
          strategy: "sliding",
          override: "",
          default: "sliding",
          choices: ["sliding", "summarize"],
          applies: true,
        });
      if (path === "/api/agents/entities") {
        if (gate.entitiesFail)
          return Promise.reject(new Error("backend is unreachable"));
        return Promise.resolve({
          entities: [
            { entity: "task", label: "tasks (add, change)" },
            { entity: "note_delete", label: "delete a note" },
          ],
          always_review: [
            "absence",
            "event_cancel",
            "memory_forget",
            "note_delete",
          ],
        });
      }
      if (path === "/api/agents")
        return Promise.resolve([
          {
            agent: "planner-agent",
            open_tasks: 0,
            pending_proposals: 0,
            last_seen: null,
            authority: gate.granted
              ? [
                  { agent: "planner-agent", entity: "task", level: "review" },
                  {
                    agent: "planner-agent",
                    entity: "note_delete",
                    level: "autonomous",
                  },
                ]
              : [],
          },
        ]);
      return Promise.resolve([]);
    },
    getUser: () => "tester",
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/agents" }));

import AgentsPage from "@/app/agents/page";
import SettingsPage from "@/app/settings/page";

const GATE_ON_CLAIM = /By default every agent write/;
const GATE_OFF_CLAIM = /The review gate is off/;
// the grants empty state carries the same rule, and said "everything an
// agent writes needs approval" directly under the corrected paragraph
const EMPTY_STATE_CLAIM =
  /No rules yet — everything an agent writes needs approval/;

beforeEach(() => {
  gate.on = false;
  gate.granted = true;
  gate.entitiesFail = false;
});

describe("the Agents page and the review gate", () => {
  it("says writes apply directly when the gate is off", async () => {
    render(<AgentsPage />);
    expect(await screen.findByText(GATE_OFF_CLAIM)).toBeTruthy();
    // the reassurance that does not hold in this configuration
    expect(screen.queryByText(GATE_ON_CLAIM)).toBeNull();
    // and the level label must not promise the checkpoint either — in BOTH
    // places it renders (the Mission control chip and the Authority grant)
    expect(
      (await screen.findAllByText(/needs approval \(gate off\)/)).length,
    ).toBe(2);
  });

  it("says writes need approval when the gate is on", async () => {
    gate.on = true;
    render(<AgentsPage />);
    expect(await screen.findByText(GATE_ON_CLAIM)).toBeTruthy();
    expect(screen.queryByText(GATE_OFF_CLAIM)).toBeNull();
    expect(screen.queryByText(/\(gate off\)/)).toBeNull();
  });

  it("does not call an empty matrix 'needs approval' when the gate is off", async () => {
    gate.granted = false;
    render(<AgentsPage />);
    expect(await screen.findByText(GATE_OFF_CLAIM)).toBeTruthy();
    // the empty state sat directly under the paragraph, contradicting it
    expect(screen.queryByText(EMPTY_STATE_CLAIM)).toBeNull();
    expect(screen.getByText(/No rules yet/)).toBeTruthy();
  });

  it("keeps the approval wording in the empty state when the gate is on", async () => {
    gate.granted = false;
    gate.on = true;
    render(<AgentsPage />);
    expect(await screen.findByText(EMPTY_STATE_CLAIM)).toBeTruthy();
  });

  it("claims neither rule while the gate state is unknown", () => {
    gate.on = "never";
    render(<AgentsPage />);
    expect(screen.queryByText(GATE_ON_CLAIM)).toBeNull();
    expect(screen.queryByText(GATE_OFF_CLAIM)).toBeNull();
    expect(screen.queryByText(/\(gate off\)/)).toBeNull();
  });
});

/** identity.force_review (backend/app/agents/identity.py) outranks the matrix
 *  AND SKEIN_AGENT_REVIEW for a flock member, so the levels this card renders
 *  do not describe a flock turn. refuse_in_flock REFUSES the four write paths
 *  that skip tools/_gate.py — those never reach the inbox, so a card promising
 *  only a queue would be false in a new direction. Neither fact depends on the
 *  gate, so the card states them in every configuration. */
describe("the authority card and a flock member", () => {
  // BOTH clauses, always. "every other level becomes a wait" is false on its
  // own — forbidden still refuses (_gate.py checks it before force_review) —
  // so the not-allowed carve-out is what makes the sentence true, and the
  // refusal clause is what keeps it from promising an inbox entry that the
  // four refuse_in_flock paths never produce.
  const FLOCK_CARVE_OUT =
    /only\s+not allowed\s+still applies from the levels\s+below/i;
  const FLOCK_CLAIM = /every other level becomes a wait in Inbox → Approvals/i;
  const FLOCK_REFUSAL = /work a delegated task or make\s+a handoff/i;

  it.each([
    ["off", false],
    ["on", true],
    ["unknown", "never"],
  ])("states the flock rule while the gate is %s", async (_label, on) => {
    gate.on = on as boolean | "never";
    const { container } = render(<AgentsPage />);
    await waitFor(() => expect(container.textContent).toMatch(FLOCK_CLAIM));
    // the wait claim never ships without EITHER clause that makes it true
    expect(container.textContent).toMatch(FLOCK_CARVE_OUT);
    expect(container.textContent).toMatch(FLOCK_REFUSAL);
  });

  it("keeps the flock rule when the record-type fetch fails", async () => {
    gate.entitiesFail = true;
    const { container } = render(<AgentsPage />);
    await waitFor(() => expect(container.textContent).toMatch(GATE_OFF_CLAIM));
    // ALWAYS_REVIEW comes from /api/agents/entities; the flock rule does not,
    // and a flock clause folded into that sentence would vanish with it
    expect(container.textContent).not.toMatch(/These still wait for a human/);
    expect(container.textContent).toMatch(FLOCK_CLAIM);
  });
});

/** Settings section 4 is where someone hands an external MCP agent their
 *  workspace. It repeated the gate-on rule unconditionally. A source scan
 *  cannot pin this — it passes even when the condition is inverted — so the
 *  page is rendered and read. */
describe("Settings when it explains what a connected agent can do", () => {
  it("does not promise a proposal queue while the gate is off", async () => {
    gate.on = false;
    const { container } = render(<SettingsPage />);
    await waitFor(() =>
      expect(container.textContent).toMatch(
        /The review gate is off in this deployment/,
      ),
    );
    expect(container.textContent).not.toMatch(/becomes a proposal/i);
  });

  it("promises the proposal queue when the gate is on", async () => {
    gate.on = true;
    const { container } = render(<SettingsPage />);
    await waitFor(() =>
      expect(container.textContent).toMatch(/becomes a proposal/i),
    );
  });
});

/** tools/_gate.py takes the review path for ALWAYS_REVIEW entities before it
 *  reads the level, so a stored "autonomous" on note_delete rendered
 *  "acts alone" over a write that always waits for a human. set_authority
 *  refuses that level now; this pins the display for any row stored before
 *  the guard existed. */
describe("a destructive entity holding a level the gate ignores", () => {
  it("never renders acts alone", async () => {
    gate.on = false;
    const { container } = render(<AgentsPage />);
    // the row now reads as its capability, so wait on that
    await waitFor(() => expect(container.textContent).toMatch(/delete a note/));
    expect(container.textContent).toMatch(/needs approval \(always\)/);
    expect(container.textContent).not.toMatch(/acts alone/);
  });
});

/** The registry keys are schema words. A matrix row grants every action the
 *  entity registers, so the row must enumerate them: "a blocker" hid that
 *  the same grant also resolves blockers (services/lexicon.py). */
describe("the authority rows", () => {
  it("name the capability, not the table", async () => {
    gate.on = false;
    const { container } = render(<AgentsPage />);
    await waitFor(() => expect(container.textContent).toMatch(/delete a note/));
    expect(container.textContent).toMatch(/tasks \(add, change\)/);
    // the raw key survives only in the tooltip, never as the row text
    expect(container.textContent).not.toMatch(/ on note_delete/);
  });
});
