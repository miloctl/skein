import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function identify(page: Page) {
  await page.goto("/");
  await page.evaluate(() => localStorage.setItem("skein-user", "ava"));
}

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
}

test("desktop navigation preserves route state and collapse preference", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await identify(page);
  await page.goto("/dashboard?view=open#browse-tasks");
  const original = page.url();
  const primary = page.getByRole("navigation", { name: "Primary", exact: true });
  await primary.getByRole("link", { name: "Browse", exact: true }).click();
  await expect(page).toHaveURL(original);
  await expect(primary.getByRole("link", { name: "Work", exact: true })).toHaveAttribute("aria-current", "location");
  await expect(primary.getByRole("link", { name: "Browse", exact: true })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("combobox", { name: "Browse register", exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Browse sections" })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Section", exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: "Collapse navigation", exact: true }).click();
  await expect(page.locator(".app-shell")).toHaveAttribute("data-collapsed", "true");
  await noOverflow(page);
  await page.reload();
  await expect(page.locator(".app-shell")).toHaveAttribute("data-collapsed", "true");
  await page.getByRole("button", { name: "Expand navigation", exact: true }).click();
  await primary.getByRole("link", { name: "Health", exact: true }).click();
  await expect(page).toHaveURL(/\/portfolio$/);
  await expect(page.getByRole("heading", { name: "Health", exact: true, level: 1 })).toBeVisible();
  const scan = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
  expect(scan.violations.map((v) => ({ id: v.id, targets: v.nodes.map((n) => n.target) }))).toEqual([]);
});

test("a navigation drawer yields to task history and same-page identity links", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 900 });
  await identify(page);
  await page.goto("/dashboard");
  const task = page.getByRole("region", { name: "Tasks", exact: true }).getByRole("button", { name: /^Open #/ }).first();
  await task.click();
  const peek = page.getByRole("dialog", { name: /^Task #/ });
  await expect(peek).toBeVisible();
  await page.goBack();
  await expect(peek).toBeHidden();
  await page.getByRole("button", { name: "Open navigation", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "Navigation", exact: true });
  await expect(drawer).toBeVisible();
  await page.goForward();
  await expect(drawer).toBeHidden();
  await expect(peek).toBeVisible();
  await expect(page.getByRole("button", { name: "Close the task panel", exact: true })).toBeFocused();
  await page.keyboard.press("Escape");

  await page.goto("/settings#settings-team");
  await page.getByRole("button", { name: "Open navigation", exact: true }).click();
  await drawer.locator("[data-identity-control]").click();
  await drawer.getByRole("menuitem", { name: "Settings & access" }).click();
  await expect(drawer).toBeHidden();
  await expect(page).toHaveURL(/\/settings#settings-you$/);
  await expect(page.getByRole("region", { name: "You", exact: true })).toBeVisible();
});

for (const width of [768, 1440]) {
  test(`Search stays beside the shell at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await identify(page);
    await page.goto("/dashboard");
    const states = width >= 1024 ? ["expanded", "collapsed"] : ["drawer"];
    for (const state of states) {
      if (state === "collapsed") await page.getByRole("button", { name: "Collapse navigation", exact: true }).click();
      const input = page.getByRole("textbox", { name: "Search Skein", exact: true });
      await input.fill("task #2");
      await input.press("Enter");
      const results = page.getByRole("region", { name: "Search results" });
      await expect(results).toBeVisible();
      const box = await results.boundingBox();
      const edge = width < 1024 ? 0 : await page.locator(".shell-sidebar").evaluate((el) => el.getBoundingClientRect().right);
      expect(box!.x).toBeGreaterThanOrEqual(edge);
      expect(box!.x + box!.width).toBeLessThanOrEqual(width);
      await page.keyboard.press("Escape");
    }
  });
}

for (const width of [320, 768]) {
  test(`navigation drawer and overlay handoff work at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await identify(page);
    await page.goto("/");
    const open = page.getByRole("button", { name: "Open navigation", exact: true });
    const drawer = page.getByRole("dialog", { name: "Navigation", exact: true });
    await open.click();
    await expect(drawer).toBeVisible();
    // Native dialogs permit browser-chrome focus, never background page controls.
    for (let index = 0; index < 18; index++) {
      await page.keyboard.press("Tab");
      expect(await drawer.evaluate((el) =>
        el.contains(document.activeElement) ||
        (!document.hasFocus() && document.activeElement === document.body && el.matches(":modal")),
      )).toBe(true);
    }
    await drawer.getByRole("button", { name: "Close navigation", exact: true }).focus();
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(open).toBeFocused();

    await open.click();
    await page.keyboard.press("Control+k");
    await expect(drawer).toBeHidden();
    await expect(page.getByRole("textbox", { name: "Search Skein", exact: true })).toBeFocused();
    await page.getByRole("button", { name: "+ Capture", exact: true }).click();
    const capture = page.getByRole("dialog", { name: "Quick capture", exact: true });
    await expect(capture).toBeVisible();
    await page.keyboard.press("Control+k");
    expect(await capture.evaluate((el) => el.contains(document.activeElement))).toBe(true);
    await page.keyboard.press("Escape");
    await expect(capture).toBeHidden();

    await open.click();
    await drawer.getByRole("link", { name: "Work", exact: true }).click();
    await expect(page).toHaveURL(/\/portfolio$/);
    await expect(drawer).toBeHidden();
    await open.click();
    await drawer.getByRole("link", { name: "Browse", exact: true }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect(drawer).toBeHidden();
    await noOverflow(page);
    await open.click();
    const scan = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
    expect(scan.violations.map((v) => ({ id: v.id, targets: v.nodes.map((n) => n.target) }))).toEqual([]);
  });
}
