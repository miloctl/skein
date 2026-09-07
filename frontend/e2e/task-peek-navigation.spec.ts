import { expect, test, type Locator, type Page } from "@playwright/test";

type CloseReceipt = { taskId: number; url: string };
declare global {
  interface Window {
    peekCloseReceipts: CloseReceipt[];
  }
}

function recordCloses() {
  const receipts: CloseReceipt[] = [];
  window.peekCloseReceipts = receipts;
  window.addEventListener("skein-peek-close", (event) => {
    receipts.push({
      taskId: (event as CustomEvent<{ taskId: number }>).detail.taskId,
      url: window.location.href,
    });
  });
}

async function openBrowse(page: Page) {
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => window.localStorage.setItem("skein-user", "ava"));
  await page.goto("/dashboard?view=open");
  await expect(page.getByRole("region", { name: "Tasks", exact: true })).toBeVisible();
  await page.waitForLoadState("networkidle");
  await page.evaluate(recordCloses);
  expect(await page.evaluate(() => document.activeElement === document.body)).toBe(true);
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#content")).toBeFocused();
  await expect(page).toHaveURL(/\/dashboard\?view=open#content$/);
  const opener = page.getByRole("region", { name: "Tasks", exact: true })
    .getByRole("button", { name: /^Open #\d+ / }).first();
  const label = await opener.innerText();
  const id = Number(/#(\d+)/.exec(label)?.[1]);
  expect(id).toBeGreaterThan(0);
  return { opener, id, returnUrl: page.url() };
}

async function expectOpen(page: Page, id: number, returnUrl: string) {
  const url = new URL(returnUrl);
  url.searchParams.set("task", String(id));
  await expect(page).toHaveURL(url.href);
  await expect(page.getByRole("dialog", { name: new RegExp(`^Task #${id}:`) })).toBeVisible();
  await expect(page.getByRole("button", { name: "Close the task panel", exact: true })).toBeFocused();
  await expect(page.locator("#content")).toHaveAttribute("inert", "");
}

async function expectClosed(page: Page, target: Locator, id: number, returnUrl: string, count: number) {
  await expect(page).toHaveURL(returnUrl);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  // Fragment traversal and React can both move focus. Check after the next
  // paint, not only the first instant the dialog leaves the DOM.
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  }));
  await expect(target).toBeFocused();
  await expect(page.locator("#content")).not.toHaveAttribute("inert", "");
  await expect.poll(() => page.evaluate(() => window.peekCloseReceipts))
    .toEqual(Array.from({ length: count }, () => ({ taskId: id, url: returnUrl })));
}

for (const width of [360, 1280]) {
  test.describe(`Task Peek history at ${width}px`, () => {
    test.use({ viewport: { width, height: 800 } });

    for (const method of ["Close", "Escape", "Back"]) {
      test(`Skip, task, ${method}, and Forward keep URL and focus`, async ({ page }) => {
        const { opener, id, returnUrl } = await openBrowse(page);
        await opener.focus();
        await page.keyboard.press("Enter");
        await expectOpen(page, id, returnUrl);

        if (method === "Close")
          await page.getByRole("button", { name: "Close the task panel", exact: true }).click();
        else if (method === "Escape") await page.keyboard.press("Escape");
        else await page.goBack();
        await expectClosed(page, opener, id, returnUrl, 1);

        await page.goForward();
        await expectOpen(page, id, returnUrl);
        await page.keyboard.press("Escape");
        await expectClosed(page, opener, id, returnUrl, 2);
      });
    }

    for (const entry of ["replace", "goto"]) {
      for (const method of ["Close", "Escape"]) {
        test(`a fresh-tab direct task link closes in place with ${method} (${entry})`, async ({ page, browser }) => {
          const { id, returnUrl } = await openBrowse(page);
          const context = await browser.newContext({
            ignoreHTTPSErrors: true,
            viewport: { width, height: 800 },
          });
          try {
            await context.addInitScript(() => localStorage.setItem("skein-user", "ava"));
            await context.addInitScript(recordCloses);
            const direct = await context.newPage();
            const url = new URL(returnUrl);
            url.searchParams.set("task", String(id));
            if (entry === "replace") {
              await direct.evaluate((href) => location.replace(href), url.href);
              await direct.waitForURL(url.href);
            } else await direct.goto(url.href);
            await expectOpen(direct, id, returnUrl);
            const historyLength = await direct.evaluate(() => history.length);
            if (entry === "replace") expect(historyLength).toBe(1);

            if (method === "Close")
              await direct.getByRole("button", { name: "Close the task panel", exact: true }).click();
            else await direct.keyboard.press("Escape");
            // The initial fragment focuses main before the panel opens, so
            // main is the connected return target, not the Search fallback.
            await expectClosed(direct, direct.locator("#content"), id, returnUrl, 1);
            expect(await direct.evaluate(() => history.length)).toBe(historyLength);
          } finally {
            await context.close();
          }
        });
      }
    }

    test("Search restores focus when its task result unmounts", async ({ page }) => {
      const { id, returnUrl } = await openBrowse(page);
      const search = page.getByLabel("Search Skein", { exact: true });
      await search.fill(`task #${id}`);
      await search.press("Enter");
      const result = page.getByRole("region", { name: "Search results" })
        .getByRole("button", { name: new RegExp(`^Open task #${id}\\b`) }).first();
      await result.focus();
      await page.keyboard.press("Enter");
      await expectOpen(page, id, returnUrl);
      await expect(result).toHaveCount(0);
      await page.keyboard.press("Escape");
      await expectClosed(page, search, id, returnUrl, 1);

      await page.goForward();
      await expectOpen(page, id, returnUrl);
      await page.goBack();
      await expectClosed(page, search, id, returnUrl, 2);
    });
  });
}
