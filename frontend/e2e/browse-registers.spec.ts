import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function selectRegister(page: Page, name: string) {
  await page.getByRole("combobox", { name: "Browse register", exact: true }).selectOption({ label: name });
}

for (const width of [360, 1440]) {
  test(`Browse preserves drafts and fragment history at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await page.evaluate(() => localStorage.setItem("skein-user", "ava"));
    await page.goto("/dashboard");
    const tasks = page.getByRole("region", { name: "Tasks", exact: true });
    await expect(tasks).toBeVisible();
    await expect(page.locator("#browse-milestones")).toBeHidden();

    await selectRegister(page, "Milestones");
    const title = page.getByLabel("New milestone title", { exact: true });
    await expect(title).toBeHidden();
    const add = page.locator("#browse-milestones summary", { hasText: "Add milestone" });
    await add.focus();
    await page.keyboard.press("Enter");
    await title.fill("Retained browser draft");
    await selectRegister(page, "Calendar");
    await expect(title).toBeHidden();
    await selectRegister(page, "Milestones");
    await expect(title).toBeVisible();
    await expect(title).toHaveValue("Retained browser draft");

    // The selector rewrites the fragment in place: no history entry per
    // option, and a keyboard step through the options keeps focus on it.
    await expect(page).toHaveURL(/#browse-milestones$/);
    const select = page.getByRole("combobox", { name: "Browse register", exact: true });
    const entries = await page.evaluate(() => history.length);
    await select.focus();
    await page.keyboard.press("ArrowDown");
    await expect(select).toBeFocused();
    expect(await page.evaluate(() => history.length)).toBe(entries);
    await select.selectOption({ label: "Milestones" });
    await page.goBack();
    await expect(page).not.toHaveURL(/\/dashboard/);
    await page.goForward();
    await expect(page).toHaveURL(/#browse-milestones$/);
    await expect(page.locator("#browse-milestones")).toBeVisible();

    const first = page.locator('#browse-milestones [id^="milestone-"]').first();
    const id = await first.getAttribute("id");
    expect(id).toMatch(/^milestone-\d+$/);
    await page.goto(`/dashboard#${id}`);
    await expect(page.locator(`#${id}`)).toBeFocused();
    await expect(tasks).toBeHidden();
    await page.goto("/dashboard#browse-calendar");
    const calendar = page.locator("#browse-calendar");
    await expect(calendar).toBeFocused();
    expect(await calendar.evaluate((el) => getComputedStyle(el).boxShadow)).not.toBe("none");
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    const scan = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
    expect(scan.violations.map((v) => ({ id: v.id, nodes: v.nodes.map((n) => n.target) }))).toEqual([]);
  });
}

test("Task warning headings retain their semantic color", async ({ page, request }) => {
  const api = process.env.SKEIN_E2E_API_URL ?? "http://127.0.0.1:8600";
  const headers = { "X-User": "calm-warning-browser" };
  const task = await request.post(`${api}/api/tasks`, { headers, data: { title: "Check task warning color" } });
  expect(task.ok()).toBe(true);
  const { id } = await task.json();
  const blocker = await request.post(`${api}/api/blockers`, {
    headers, data: { title: "Waiting for a test decision", task_id: id },
  });
  expect(blocker.ok()).toBe(true);
  await page.goto("/");
  await page.evaluate(() => localStorage.setItem("skein-user", "calm-warning-browser"));
  await page.goto(`/dashboard?task=${id}`);
  const warning = page.getByRole("heading", { name: "Blocked by", exact: true });
  await expect(warning).toBeVisible();
  expect(await warning.evaluate((el) => {
    const color = getComputedStyle(el).color;
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d")!;
    context.fillStyle = getComputedStyle(el).getPropertyValue("--weld").trim();
    const token = context.fillStyle;
    context.fillStyle = color;
    return context.fillStyle === token;
  })).toBe(true);
});

test("Planning owns the real draft and commitment flow", async ({ page, request }) => {
  const api = process.env.SKEIN_E2E_API_URL ?? "http://127.0.0.1:8600";
  const user = "planning-browser";
  const response = await request.post(`${api}/api/capture`, {
    headers: { "X-User": user },
    data: { text: "todo: Check the weekly planning flow" },
  });
  expect(response.ok()).toBe(true);
  await page.goto("/");
  await page.evaluate((name) => localStorage.setItem("skein-user", name), user);
  await page.goto("/portfolio");
  await expect(page.getByRole("button", { name: "Draft a plan", exact: true })).toHaveCount(0);
  await page.locator('main a[href="/planning#planning-this-week"]').click();
  await expect(page.locator("#planning-this-week")).toBeFocused();
  await page.getByRole("button", { name: "Draft a plan", exact: true }).click();
  await expect(page.getByText("Check the weekly planning flow", { exact: false }).last()).toBeVisible();
  await page.getByRole("button", { name: /^Add \d+ tasks? to the plan$/ }).click();
  await expect(page.getByRole("status").filter({ hasText: /task.*added to/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Add \d+ tasks? to the plan$/ })).toHaveCount(0);
});
