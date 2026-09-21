import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const API = process.env.SKEIN_E2E_API_URL ?? "http://127.0.0.1:8600";

for (const width of [360, 1440]) {
  test(`Recent changes reviews a displayed batch at ${width}px`, async ({ page, request }) => {
    const user = `recent-browser-${width}`;
    const headers = { "X-User": user };
    const initial = await request.get(`${API}/api/delta`, { headers });
    expect(initial.ok()).toBe(true);
    const window = await initial.json();
    const due = new Date(`${window.window_end}T00:00:00Z`);
    due.setUTCDate(due.getUTCDate() - 1);
    const create = (label: string) => request.post(`${API}/api/promises`, {
      headers,
      data: { promise: label, to_whom: label, due_date: due.toISOString().slice(0, 10) },
    });
    expect((await create(`Summary test ${width}`)).ok()).toBe(true);

    let acknowledgments = 0;
    page.on("request", (req) => {
      if (req.method() === "POST" && new URL(req.url()).pathname === "/api/delta/ack") acknowledgments += 1;
    });
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await page.evaluate((name) => localStorage.setItem("skein-user", name), user);
    // A fragment-only goto keeps the anonymous document's session bootstrap.
    await page.reload();
    await page.goto("/#recent-changes");
    const heading = page.getByRole("heading", { name: "Recent changes", exact: true });
    await expect(heading).toBeFocused();
    const summary = heading.locator("..");
    await expect(summary.getByText(`${window.window_start} to ${window.window_end}`, { exact: false })).toBeVisible();
    await expect(summary.getByRole("button", { name: "Mark this summary reviewed", exact: true })).toBeVisible();
    const visibleRows = summary.locator("li:visible");
    expect(await visibleRows.count()).toBeLessThanOrEqual(5);
    const adoption = summary.locator("summary").filter({ hasText: /^Feature adoption/ });
    if (await adoption.count()) {
      await adoption.focus();
      await page.keyboard.press("Enter");
      await expect(adoption.locator("..")).toHaveAttribute("open", "");
      await page.keyboard.press("Enter");
    }
    expect(acknowledgments).toBe(0);
    const before = await (await request.get(`${API}/api/delta`, { headers })).json();
    expect(before.reviewed).toBe(false);

    const mark = summary.getByRole("button", { name: "Mark this summary reviewed", exact: true });
    await mark.focus();
    await page.keyboard.press("Enter");
    await expect(summary.getByText("This summary is reviewed.", { exact: true })).toBeVisible();
    await expect(summary.getByRole("button", { name: "Reopen summary", exact: true })).toBeFocused();
    expect(acknowledgments).toBe(1);
    const reviewed = await (await request.get(`${API}/api/delta`, { headers })).json();
    expect(reviewed.reviewed).toBe(true);
    await page.reload();
    await expect(page.getByRole("button", { name: "Reopen summary", exact: true })).toBeVisible();

    expect((await create(`Later summary test ${width}`)).ok()).toBe(true);
    await summary.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(summary.getByRole("button", { name: "Mark this summary reviewed", exact: true })).toBeVisible();
    expect(acknowledgments).toBe(1);
    const changed = await (await request.get(`${API}/api/delta`, { headers })).json();
    expect(changed.snapshot_id).not.toBe(reviewed.snapshot_id);
    expect(changed.reviewed).toBe(false);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    // Axe counts targets clipped behind the sticky header after fragment focus.
    // responsive.spec.ts audits at the page origin after checking keyboard focus.
    await page.evaluate(() => scrollTo({ top: 0, behavior: "instant" }));
    const scan = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
    expect(scan.violations.map((v) => ({ id: v.id, targets: v.nodes.map((n) => n.target) }))).toEqual([]);
  });
}
