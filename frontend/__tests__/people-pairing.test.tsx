import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

/** The People page fetches a brief only under an accepted pairing, and never
 *  shows one person's private notes under another person's chip. */

const state = vi.hoisted(() => ({
  pairs: null as null | { leading: unknown[]; subject_of: unknown[] },
  pairsGate: null as Promise<void> | null,
  postGate: null as Promise<void> | null,
  calls: [] as string[],
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: (path: string, init?: { method?: string }) => {
    state.calls.push(`${init?.method ?? "GET"} ${path}`);
    if (init?.method === "POST")
      return (state.postGate ?? Promise.resolve()).then(() => ({ id: 5, status: "proposed" }));
    if (path === "/api/whoami") return Promise.resolve({ strong: true, user: "me" });
    if (path === "/api/users")
      return Promise.resolve(["me", "alice", "bob"].map((name) => ({ name, kind: "human" })));
    if (path === "/api/private/pairs")
      // a fresh object, as a real response is: React skips an identical one
      return (state.pairsGate ?? Promise.resolve()).then(() => structuredClone(state.pairs));
    if (path.startsWith("/api/private/notes")) {
      const person = path.split("=")[1];
      return Promise.resolve([
        { id: 1, person, kind: "note", body: `Saved for ${person}`, created_at: "2026-09-04" },
      ]);
    }
    return Promise.resolve({
      person: path.split("/").pop(), since: "2026-09-01", standups: [], open_blockers: [],
      open_questions: [], in_progress: [], recently_done: [], promises_made: [],
      feedback_gap_days: null, nudge: "",
    });
  },
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/people" }));
import PeoplePage from "@/app/people/page";

const briefs = (person: string) =>
  state.calls.filter((c) => c === `GET /api/private/brief/${person}`).length;

beforeEach(() => {
  state.pairs = {
    leading: [{ id: 1, subject: "bob", status: "accepted" }],
    subject_of: [{ id: 7, lead: "alice", status: "proposed", last_brief_at: null }],
  };
  state.pairsGate = null;
  state.postGate = null;
  state.calls = [];
});

it("never loads the person a pairing action started on after a switch", async () => {
  let release = () => {};
  state.postGate = new Promise((resolve) => (release = resolve));
  render(<PeoplePage />);
  fireEvent.click(await screen.findByRole("button", { name: "alice" }));
  fireEvent.click(await screen.findByRole("button", { name: "Ask for a 1:1 pairing" }));
  fireEvent.click(screen.getByRole("button", { name: "bob" }));
  await act(async () => release());
  await screen.findByText("Saved for bob");
  await act(async () => {});
  expect(screen.queryByText("Saved for alice")).toBeNull();
});

it("pulls no brief without a pairing, and does not re-pull an unchanged one", async () => {
  render(<PeoplePage />);
  fireEvent.click(await screen.findByRole("button", { name: "alice" }));
  await screen.findByText(/Their brief opens after alice accepts/);
  expect(briefs("alice")).toBe(0);
  fireEvent.click(screen.getByRole("button", { name: "bob" }));
  await waitFor(() => expect(briefs("bob")).toBe(1));
  // a pairing action about someone else refreshes the pairs, not bob's brief
  fireEvent.click(screen.getByRole("button", { name: "Accept the 1:1 pairing with alice" }));
  await waitFor(() => expect(state.calls).toContain("POST /api/private/pairs/7/accept"));
  await act(async () => {});
  expect(briefs("bob")).toBe(1);
});

it("opens your own brief without a pairing", async () => {
  render(<PeoplePage />);
  fireEvent.click(await screen.findByRole("button", { name: "me" }));
  await waitFor(() => expect(briefs("me")).toBe(1));
  expect(screen.queryByRole("button", { name: "Ask for a 1:1 pairing" })).toBeNull();
});

it("says nothing about a pairing it has not read yet", async () => {
  state.pairsGate = new Promise(() => {});
  render(<PeoplePage />);
  fireEvent.click(await screen.findByRole("button", { name: "bob" }));
  await act(async () => {});
  expect(screen.queryByRole("button", { name: "Ask for a 1:1 pairing" })).toBeNull();
  expect(screen.getAllByText("Loading…").length).toBeGreaterThan(0);
});

it("drops the previous owner's pairings on an identity change", async () => {
  render(<PeoplePage />);
  await screen.findByText("alice asks to prepare 1:1s with you.");
  state.pairsGate = new Promise(() => {});
  await act(async () => window.dispatchEvent(new Event("skein-identity-change")));
  expect(screen.queryByText("alice asks to prepare 1:1s with you.")).toBeNull();
  fireEvent.click(await screen.findByRole("button", { name: "bob" }));
  await act(async () => {});
  expect(briefs("bob")).toBe(0);
});
