import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/** A card moves by drag and by keyboard alone, through the same rule, and
 *  the board stays clean for axe (docs/intent/board-view.md slice 4). */
test("a card moves by drag, then by keyboard alone", async ({ page, request }) => {
  const api = process.env.SKEIN_E2E_API_URL ?? "http://127.0.0.1:8600";
  const user = "board-mover";
  const headers = { "X-User": user };
  const created = await request.post(`${api}/api/tasks`, {
    headers,
    data: { title: "Move me across the board", assignee: user },
  });
  expect(created.ok()).toBe(true);
  const { id } = await created.json();

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page.evaluate((name) => localStorage.setItem("skein-user", name), user);
  await page.goto("/board");
  await page.getByRole("checkbox", { name: "Only my tasks" }).check();
  const column = (name: string) => page.getByRole("region", { name: new RegExp(`^${name} \\(`) });
  const card = (name: string) => column(name).getByRole("listitem").filter({ hasText: "Move me across the board" });
  await expect(card("To do")).toBeVisible();

  await card("To do").dragTo(column("In progress"));
  await expect(card("In progress")).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: `Task #${id} moved to In progress.` })).toBeVisible();

  const move = page.locator(`#board-move-${id}`);
  await move.focus();
  await page.keyboard.press("Enter");
  // the panel takes focus on its first choice, To do
  await expect(page.getByRole("button", { name: "To do", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(card("To do")).toBeVisible();
  await expect(move).toBeFocused();

  const scan = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
  expect(scan.violations.map((v) => ({ id: v.id, nodes: v.nodes.map((n) => n.target) }))).toEqual([]);
});
