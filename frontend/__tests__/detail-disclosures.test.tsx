import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import responses from "./fixtures/calmer-detail-responses.json";

const mocks = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock("@/lib/api", async (original) => ({
  ...await original<typeof import("@/lib/api")>(),
  api: mocks.api,
  getUser: () => "ava",
  subscribeUser: () => () => {},
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/agents" }));
import Agents from "@/app/agents/page";
import Review from "@/app/review/page";
import { TaskPeek } from "@/components/task-peek";

beforeEach(() => {
  window.history.replaceState({}, "", "/agents");
  mocks.api.mockReset();
  mocks.api.mockImplementation((path: string) => {
    if (path === "/api/personas") return Promise.resolve(responses.personas);
    if (path === "/api/agents/status") return Promise.resolve(responses.status);
    if (path === "/api/agents/entities") return Promise.resolve({ entities: [], always_review: [] });
    if (path.startsWith("/api/review?status=pending")) return Promise.resolve(responses.pending);
    if (path === "/api/tasks/1") return Promise.resolve(responses.task);
    return Promise.resolve([]);
  });
});

it("keeps specialist purpose and Chat visible with command details outside links", async () => {
  render(<Agents />);
  const name = await screen.findByRole("heading", { name: "Backend Architect" });
  const row = name.closest("li")!;
  expect(within(row).getByText(responses.personas[0].description)).toBeTruthy();
  expect(within(row).getByRole("link", { name: /Chat/ }).getAttribute("href")).toBe("/chat?as=backend-architect");
  const details = within(row).getByText("Details").closest("details")!;
  expect(details.open).toBe(false);
  expect(details.closest("a")).toBeNull();
  expect(within(row).getByText(responses.personas[0].vibe).closest("details")?.open).toBe(false);
  fireEvent.click(within(row).getByText("Details"));
  expect(within(row).getByText(responses.personas[0].vibe).closest("details")?.open).toBe(true);
});

it("gives each proposal a heading without hiding its acceptance evidence", async () => {
  render(<Review />);
  const heading = await screen.findByRole("heading", { name: /#2 · mark a delegated task done/ });
  const row = heading.closest("li")!;
  expect(within(row).getByText("pulled 4 of 6 pricing pages; two need JS rendering").closest("details")).toBeNull();
  expect(within(row).getByText(/^by research-agent/).closest("h2")).toBeNull();
  expect(within(row).getByRole("button", { name: /Accept for mario proposal/ })).toBeTruthy();
});

it("keeps task actions visible while passive relationships wait in Task details", async () => {
  window.history.replaceState({}, "", "/?task=1");
  render(<TaskPeek />);
  await screen.findByRole("dialog", { name: /Interview the requester/ });
  const summary = screen.getByText("Task details");
  expect(summary.closest("details")!.open).toBe(false);
  expect(screen.getByText("Scope & success criteria").closest("details")?.open).toBe(false);
  expect(screen.getByRole("button", { name: /edit…/ })).toBeTruthy();
  expect(screen.getByText("done")).toBeTruthy();
  fireEvent.click(summary);
  expect(screen.getByText("Scope & success criteria").closest("details")?.open).toBe(true);
});
