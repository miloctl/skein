import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** The Agents page answers one question: can an agent write without asking?
 *  The answer INVERTS with the review gate, which ships off by default
 *  (config.py SKEIN_AGENT_REVIEW). tools/_gate.py takes the direct path on
 *  `not config.AGENT_REVIEW` whatever the authority level, so with the gate
 *  off autonomous, notify and review all mean "acts alone" and only
 *  forbidden stops a write. The page used to state the gate-on rule
 *  unconditionally — a false reassurance about a safety control, in the
 *  configuration that is the default. */

const gate: { on: boolean | "never"; granted: boolean } = { on: false, granted: true };

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
      if (path === "/api/agents")
        return Promise.resolve([
          {
            agent: "planner-agent",
            open_tasks: 0,
            pending_proposals: 0,
            last_seen: null,
            authority: gate.granted
              ? [{ agent: "planner-agent", entity: "task", level: "review" }]
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

const GATE_ON_CLAIM = /By default every agent write/;
const GATE_OFF_CLAIM = /The review gate is off/;
// the grants empty state carries the same rule, and said "everything an
// agent writes needs approval" directly under the corrected paragraph
const EMPTY_STATE_CLAIM = /No rules yet — everything an agent writes needs approval/;

beforeEach(() => {
  gate.on = false;
  gate.granted = true;
});

describe("the Agents page and the review gate", () => {
  it("says writes apply directly when the gate is off", async () => {
    render(<AgentsPage />);
    expect(await screen.findByText(GATE_OFF_CLAIM)).toBeTruthy();
    // the reassurance that does not hold in this configuration
    expect(screen.queryByText(GATE_ON_CLAIM)).toBeNull();
    // and the level label must not promise the checkpoint either — in BOTH
    // places it renders (the Mission control chip and the Authority grant)
    expect((await screen.findAllByText(/needs approval \(gate off\)/)).length).toBe(2);
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
