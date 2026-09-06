import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ save: vi.fn(), strong: true }));
vi.mock("next/navigation", () => ({ usePathname: () => "/people" }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  api: (path: string, init?: RequestInit) => {
    if (path === "/api/whoami") return Promise.resolve({ strong: state.strong });
    if (path === "/api/users") return Promise.resolve([
      { name: "alice", kind: "human" }, { name: "bob", kind: "human" },
    ]);
    if (init?.method === "POST") return state.save(JSON.parse(String(init.body)));
    if (path.startsWith("/api/private/notes")) return Promise.resolve([{
      id: 1, person: path.split("=")[1], kind: "note",
      body: `Saved for ${path.split("=")[1]}`, created_at: "2026-09-04",
    }]);
    return Promise.resolve({
      person: path.split("/").pop(), since: "2026-09-01", standups: [],
      open_blockers: [], open_questions: [], in_progress: [], recently_done: [],
      promises_made: [], feedback_gap_days: null, nudge: "",
    });
  },
}));
import PeoplePage from "@/app/people/page";

beforeEach(() => { state.strong = true; state.save.mockReset().mockResolvedValue({}); });

it("keeps each teammate's draft separate and clears drafts on identity change", async () => {
  render(<PeoplePage />);
  fireEvent.click(await screen.findByRole("button", { name: "alice" }));
  fireEvent.change(screen.getByLabelText("1:1 note"), { target: { value: "Alice draft" } });
  fireEvent.click(screen.getByRole("button", { name: "bob" }));
  expect((screen.getByLabelText("1:1 note") as HTMLInputElement).value).toBe("");
  fireEvent.click(screen.getByRole("button", { name: "alice" }));
  expect((screen.getByLabelText("1:1 note") as HTMLInputElement).value).toBe("Alice draft");
  await act(async () => window.dispatchEvent(new Event("storage")));
  fireEvent.click(await screen.findByRole("button", { name: "alice" }));
  expect((screen.getByLabelText("1:1 note") as HTMLInputElement).value).toBe("");
});

it("does not replace another teammate's notes or draft when an earlier save finishes", async () => {
  let finish!: (value: object) => void;
  state.save.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  render(<PeoplePage />);
  fireEvent.click(await screen.findByRole("button", { name: "alice" }));
  fireEvent.change(screen.getByLabelText("1:1 note"), { target: { value: "Alice draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  fireEvent.click(screen.getByRole("button", { name: "bob" }));
  fireEvent.change(screen.getByLabelText("1:1 note"), { target: { value: "Bob draft" } });
  await screen.findByText("Saved for bob");
  await act(async () => finish({}));
  expect(screen.getByText("Saved for bob")).toBeTruthy();
  expect(screen.queryByText("Saved for alice")).toBeNull();
  expect((screen.getByLabelText("1:1 note") as HTMLInputElement).value).toBe("Bob draft");
});

it("keeps visible labels for private note type and body", async () => {
  render(<PeoplePage />);
  fireEvent.click(await screen.findByRole("button", { name: "alice" }));
  for (const name of ["Note type", "1:1 note"]) {
    const field = screen.getByLabelText(name) as HTMLInputElement;
    expect(field.labels?.length).toBe(1);
    expect(field.labels?.[0].classList.contains("sr-only")).toBe(false);
  }
});
