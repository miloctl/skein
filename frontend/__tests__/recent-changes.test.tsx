import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import captured from "./fixtures/recent-changes.json";

const mocks = vi.hoisted(() => ({ read: vi.fn(), acknowledge: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  api: mocks.read,
  authenticatedFetch: mocks.acknowledge,
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

import { RecentChanges } from "@/components/recent-changes";

// GET /api/delta as mario on the disposable seeded mock server at :8600, after
// six promises due yesterday, the health-snapshot job, and POST /api/findings/run.
const acceptance = captured.items.find((item) => item.kind === "acceptance_waiting")!;
const adoption = captured.items.filter((item) => item.rule_id === "feature_unadopted");
const finding = captured.items.find((item) => item.rule_id === "promise_due")!;
const ack = () => Response.json({ snapshot_id: captured.snapshot_id, review_revision: 1, reviewed: true });
const primaryLinks = () => screen.queryAllByRole("link").filter((link) => !link.closest("details"));
const loaded = () => screen.findByText(`${captured.items.length} changes in this summary`);

beforeEach(() => {
  mocks.read.mockReset().mockResolvedValue(structuredClone(captured));
  mocks.acknowledge.mockReset().mockImplementation(async () => ack());
  window.history.replaceState(null, "", "/");
});

describe("Recent changes", () => {
  it("keeps adoption last and folded, with the actual team date window and acceptance first", async () => {
    render(<RecentChanges />);
    await loaded();
    expect(screen.getByRole("heading", { name: "Recent changes", level: 2 })).toBeTruthy();
    expect(screen.getByText(`${captured.window_start} to ${captured.window_end} · Team dates`)).toBeTruthy();
    const links = primaryLinks();
    expect(links).toHaveLength(5);
    expect(screen.getByRole("heading", { name: "Overdue team promises (6)" })).toBeTruthy();
    expect(links.slice(1).map((link) => link.textContent)).toEqual(captured.items.filter((item) => item.kind === "promise_broke").slice(0, 4).map((item) => item.headline));
    expect(links[0].textContent).toBe(acceptance.headline);
    expect(links[0].getAttribute("href")).toBe(`/review?id=${acceptance.entity_id}`);
    const group = screen.getByText(`Feature adoption (${adoption.length})`).closest("details")!;
    expect(group.open).toBe(false);
    expect(group.parentElement?.nextElementSibling).toBeNull();
    expect(screen.queryByText(/Worsening health/)).toBeNull();
    expect(screen.queryByRole("button", { name: /approve|reject|accept/i })).toBeNull();
  });

  it("keeps every returned headline and receipt reachable exactly once without implicit review", async () => {
    const { container } = render(<RecentChanges />);
    await loaded();
    for (const disclosure of container.querySelectorAll("details")) fireEvent.click(disclosure.querySelector("summary")!);
    for (const item of captured.items) {
      expect(screen.getAllByRole("link", { name: item.headline })).toHaveLength(1);
      for (const receipt of item.receipts) {
        const evidence = Array.from(container.querySelectorAll("details > span"), (node) => {
          const copy = node.cloneNode(true) as HTMLElement;
          copy.querySelectorAll(".sr-only").forEach((hint) => hint.remove());
          return copy.textContent;
        });
        expect(evidence.filter((text) => text === receipt.message)).toHaveLength(1);
      }
    }
    expect(mocks.acknowledge).not.toHaveBeenCalled();
    expect(mocks.read).toHaveBeenCalledTimes(1);
    expect(mocks.read).toHaveBeenCalledWith("/api/delta", { cache: "no-store" });
  });

  it("uses structured adoption metadata, not low severity or headline text", async () => {
    // The server folds adoption into one row. Variants keep its headline and change only metadata.
    const [folded] = adoption;
    mocks.read.mockResolvedValue({ ...captured, items: [
      { ...folded, entity_id: 1, severity: "high" },
      { ...folded, entity_id: 2, rule_id: "promise_due" },
      { ...finding, severity: "low" },
      folded,
    ] });
    render(<RecentChanges />);
    await screen.findByText("4 changes in this summary");
    expect(screen.getByText("Feature adoption (1)")).toBeTruthy();
    expect(primaryLinks().map((link) => link.textContent)).toEqual([folded.headline, folded.headline, finding.headline]);
  });

  it("retains unknown and legacy rows without exposing unsupported review", async () => {
    const legacy = { ...finding, rule_id: undefined, severity: undefined };
    mocks.read.mockResolvedValue({ since: captured.since, items: [legacy, { ...acceptance, kind: "future_kind" }, { ...adoption[0], rule_id: "future_rule", severity: "future_severity" }] });
    render(<RecentChanges />);
    await screen.findByText("3 changes in this summary");
    expect(screen.getByRole("heading", { name: "Other changes (3)" })).toBeTruthy();
    expect(primaryLinks().map((link) => link.textContent)).toEqual([finding.headline, acceptance.headline, adoption[0].headline]);
    expect(screen.queryByRole("button", { name: "Mark this summary reviewed" })).toBeNull();
    expect(screen.getByText("The server did not provide the date window.")).toBeTruthy();
    expect(mocks.acknowledge).not.toHaveBeenCalled();
  });

  it("reviews only after an explicit action, once, and keeps focus on the same control", async () => {
    let finish!: (response: Response) => void;
    mocks.acknowledge.mockReturnValue(new Promise<Response>((resolve) => { finish = resolve; }));
    const attention = vi.fn();
    window.addEventListener("skein-attention-change", attention);
    render(<RecentChanges />);
    await loaded();
    expect(screen.getByText(/including collapsed details/)).toBeTruthy();
    const button = screen.getByRole("button", { name: "Mark this summary reviewed" });
    button.focus();
    fireEvent.click(button);
    fireEvent.click(button);
    expect(mocks.acknowledge).toHaveBeenCalledTimes(1);
    expect(mocks.acknowledge).toHaveBeenCalledWith("/api/delta/ack", {
      method: "POST", body: JSON.stringify({ snapshot_id: captured.snapshot_id, review_revision: captured.review_revision }),
    });
    await act(async () => finish(ack()));
    expect(screen.getByRole("button", { name: "Reopen summary" })).toBe(button);
    expect(document.activeElement).toBe(button);
    expect(primaryLinks()).toHaveLength(0);
    fireEvent.click(button);
    expect(primaryLinks().length).toBeGreaterThan(0);
    expect(mocks.acknowledge).toHaveBeenCalledTimes(1);
    expect(attention).not.toHaveBeenCalled();
    window.removeEventListener("skein-attention-change", attention);
  });

  it("opens an unchanged reviewed response compactly without another write", async () => {
    mocks.read.mockResolvedValue({ ...captured, reviewed: true, review_revision: 1 });
    render(<RecentChanges />);
    await loaded();
    expect(primaryLinks()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Reopen summary" }));
    expect(primaryLinks().length).toBeGreaterThan(0);
    expect(mocks.acknowledge).not.toHaveBeenCalled();
  });

  it("shows a load failure and retries without inventing an empty summary", async () => {
    mocks.read.mockRejectedValueOnce(new Error("Summary unavailable."));
    render(<RecentChanges />);
    expect((await screen.findByRole("alert")).textContent).toContain("Summary unavailable.");
    expect(screen.queryByText(/No recent changes/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await loaded();
    expect(mocks.acknowledge).not.toHaveBeenCalled();
  });

  it("preserves the batch after a failed acknowledgment and permits retry", async () => {
    mocks.acknowledge.mockRejectedValueOnce(new Error("Review unavailable."));
    render(<RecentChanges />);
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Mark this summary reviewed" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Review unavailable.");
    expect(screen.getByRole("link", { name: acceptance.headline })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Mark this summary reviewed" }));
    await screen.findByRole("button", { name: "Reopen summary" });
    expect(mocks.acknowledge).toHaveBeenCalledTimes(2);
  });

  it("requires a refresh after a changed-batch conflict", async () => {
    mocks.acknowledge.mockResolvedValueOnce(Response.json({ detail: "The summary changed." }, { status: 409 }));
    render(<RecentChanges />);
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Mark this summary reviewed" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Refresh the summary");
    fireEvent.click(screen.getByRole("button", { name: "Mark this summary reviewed" }));
    expect(mocks.acknowledge).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("link", { name: acceptance.headline })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(screen.queryByText("This summary is reviewed.")).toBeNull();
  });

  it("does not let batch A acknowledgment clear replacement batch B", async () => {
    let finish!: (response: Response) => void;
    mocks.acknowledge.mockReturnValue(new Promise<Response>((resolve) => { finish = resolve; }));
    render(<RecentChanges />);
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Mark this summary reviewed" }));
    mocks.read.mockResolvedValue({ ...captured, snapshot_id: "b".repeat(64), items: [finding] });
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText("1 change in this summary");
    await act(async () => finish(ack()));
    expect(screen.queryByText("This summary is reviewed.")).toBeNull();
    expect(screen.getByRole("link", { name: finding.headline })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Mark this summary reviewed" }).getAttribute("aria-disabled")).toBe("false");
  });

  it("ignores an acknowledgment body that finishes after unmount", async () => {
    let finish!: (response: Response) => void;
    mocks.acknowledge.mockReturnValue(new Promise<Response>((resolve) => { finish = resolve; }));
    const first = render(<RecentChanges />);
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Mark this summary reviewed" }));
    first.unmount();
    render(<RecentChanges />);
    await loaded();
    await act(async () => finish(ack()));
    expect(screen.queryByText("This summary is reviewed.")).toBeNull();
    expect(primaryLinks().length).toBeGreaterThan(0);
  });

  it("labels incomplete coverage and refuses review without hiding the returned rows", async () => {
    mocks.read.mockResolvedValue({ ...captured, truncated: true });
    render(<RecentChanges />);
    await screen.findByText(`${captured.items.length} changes in this summary · Partial summary`);
    expect(screen.getByText(/Some changes are not included/)).toBeTruthy();
    const button = screen.getByRole("button", { name: "Mark this summary reviewed" });
    expect(button.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(button);
    expect(mocks.acknowledge).not.toHaveBeenCalled();
    expect(primaryLinks()).toHaveLength(5);
    expect(screen.queryByText(/all updates/i)).toBeNull();
  });

  it("ignores a delayed acknowledgment body after a refresh replaces the batch", async () => {
    let finish!: (body: unknown) => void;
    const body = new Promise((resolve) => { finish = resolve; });
    mocks.acknowledge.mockResolvedValue({ ok: true, json: () => body });
    render(<RecentChanges />);
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: "Mark this summary reviewed" }));
    await act(async () => {});
    mocks.read.mockResolvedValue({ ...captured, snapshot_id: "b".repeat(64), items: [finding] });
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText("1 change in this summary");
    await act(async () => finish({ snapshot_id: captured.snapshot_id, review_revision: 1, reviewed: true }));
    expect(screen.queryByText("This summary is reviewed.")).toBeNull();
    expect(primaryLinks()).toHaveLength(1);
  });

  it("ignores the stale preview from a replaced effect generation", async () => {
    let finish!: (body: unknown) => void;
    mocks.read.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    mocks.read.mockResolvedValue({ ...captured, items: [finding] });
    render(<StrictMode><RecentChanges /></StrictMode>);
    await screen.findByText("1 change in this summary");
    await act(async () => finish(captured));
    expect(screen.getByText("1 change in this summary")).toBeTruthy();
    expect(primaryLinks()).toHaveLength(1);
    expect(mocks.acknowledge).not.toHaveBeenCalled();
  });

  it("keeps an empty summary quiet without a review action", async () => {
    mocks.read.mockResolvedValue({ ...captured, items: [], quiet: true });
    render(<RecentChanges />);
    await screen.findByText("No recent changes in this summary.");
    expect(screen.getByRole("heading", { name: "Recent changes" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Mark this summary reviewed" })).toBeNull();
    expect(screen.queryByText(/including collapsed details/)).toBeNull();
  });

  it("keeps a malformed legacy item collection from crashing My Day", async () => {
    mocks.read.mockResolvedValue({ items: {} });
    render(<RecentChanges />);
    await screen.findByText("No recent changes in this summary.");
    expect(screen.getByRole("heading", { name: "Recent changes" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Mark this summary reviewed" })).toBeNull();
  });

  it("focuses the stable visible heading for the guide fragment", async () => {
    window.history.replaceState(null, "", "/#recent-changes");
    render(<RecentChanges />);
    await loaded();
    expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Recent changes" }));
  });
});
