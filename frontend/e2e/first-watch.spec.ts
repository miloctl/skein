import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";

type Fault = { kind: string; detail: string };

function watch(page: Page): Fault[] {
  const faults: Fault[] = [];
  page.on("console", (message) => {
    if (message.type() === "error")
      faults.push({ kind: "console", detail: message.text() });
  });
  page.on("pageerror", (error) =>
    faults.push({ kind: "pageerror", detail: String(error) }),
  );
  page.on("response", (response) => {
    if (response.status() >= 400)
      faults.push({
        kind: "request",
        detail: `${response.status()} ${response.url()}`,
      });
  });
  return faults;
}

async function tabTo(page: Page, target: Locator, limit = 60) {
  await expect(target).toBeVisible();
  for (let index = 0; index < limit; index += 1) {
    if (await target.evaluate((element) => element === document.activeElement)) return;
    await page.keyboard.press("Tab");
  }
  await expect(target).toBeFocused();
}

async function expectAxeClean(page: Page) {
  const scan = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(
    scan.violations.map((violation) => ({
      rule: violation.id,
      impact: violation.impact,
      nodes: violation.nodes.map((node) => node.target.join(" ")).slice(0, 5),
    })),
  ).toEqual([]);
}

async function identify(page: Page) {
  await page.goto("/");
  await page.evaluate(() => window.localStorage.setItem("skein-user", "ava"));
}

test("First Watch carries one real task through Skein by keyboard", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await identify(page);
  const faults = watch(page);
  await page.goto("/?tour=first-watch");

  const intro = page.getByRole("heading", { name: "Bosun’s First Watch" });
  await expect(intro).toBeFocused();
  await expectAxeClean(page);

  const start = page
    .getByRole("complementary", { name: "Bosun’s First Watch" })
    .getByRole("button", { name: "Start First Watch" });
  await tabTo(page, start);
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("heading", { name: /First Watch, step 1 of 6/ }),
  ).toBeFocused();

  const openCapture = page.getByRole("button", { name: "Open Capture" });
  await tabTo(page, openCapture);
  await page.keyboard.press("Enter");
  const capture = page.getByRole("dialog", { name: "Quick capture" });
  await expect(capture).toBeVisible();
  await expectAxeClean(page);
  const captureInput = page.getByLabel("What to capture");
  await expect(captureInput).toBeFocused();
  await expect(captureInput).toHaveValue("todo: ");
  await page.keyboard.type("First Watch browser task");
  await page.keyboard.press("Tab");
  await expect(
    capture.getByRole("button", { name: "Capture", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(capture).toBeHidden();

  const work = page.getByRole("button", { name: "Continue to Work" });
  await expect(work).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/dashboard/);
  await expect(
    page.getByRole("heading", { name: /First Watch, step 2 of 6/ }),
  ).toBeFocused();

  const openTask = page.getByRole("button", { name: /Open task #\d+/ });
  await tabTo(page, openTask);
  await page.keyboard.press("Enter");
  const taskPanel = page.getByRole("dialog", { name: /Task #\d+:/ });
  await expect(taskPanel).toBeVisible();
  await expectAxeClean(page);
  await expect(page.getByRole("button", { name: "Close the task panel" })).toBeFocused();
  await page.keyboard.press("Escape");

  const continueSearch = page.getByRole("button", { name: "Continue to Search" });
  await expect(continueSearch).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("heading", { name: /First Watch, step 3 of 6/ }),
  ).toBeFocused();

  const putInSearch = page.getByRole("button", { name: /Put #\d+ in Search/ });
  await tabTo(page, putInSearch);
  await page.keyboard.press("Enter");
  const search = page.getByLabel("Search Skein");
  await expect(search).toBeFocused();
  await page.keyboard.press("Enter");
  const results = page.getByRole("region", { name: "Search results" });
  await expect(results).toBeVisible();
  await expectAxeClean(page);
  const result = results.getByRole("button", { name: /First Watch browser task/ });
  await tabTo(page, result);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: /First Watch browser task/ })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(search).toBeFocused();
  await expect(page.getByTestId("first-watch-status")).toHaveText(
    "Task found in Search. Continue to Inbox.",
  );

  const inbox = page.getByRole("link", { name: "Continue to Inbox" });
  await tabTo(page, inbox);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/review/);
  const team = page.getByRole("link", { name: "Continue to Team" });
  await tabTo(page, team);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/activity/);
  const chat = page.getByRole("button", { name: "Continue to Chat" });
  await tabTo(page, chat);
  await page.keyboard.press("Enter");

  const help = page.getByRole("link", { name: "Open Chat help" });
  await tabTo(page, help);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/chat/);
  const composer = page.getByRole("combobox");
  await expect(composer).toHaveValue("/help");
  await expect(composer).toBeFocused();
  await expect(page.getByRole("complementary", { name: /First Watch/ })).toHaveCount(0);
  await expectAxeClean(page);

  expect(faults, JSON.stringify(faults, null, 2)).toEqual([]);
});

test("First Watch remains inside a short phone viewport at larger text", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 320 });
  await identify(page);
  await page.goto("/?tour=first-watch");
  await page.addStyleTag({
    content:
      "aside[aria-labelledby='first-watch-title'], " +
      "aside[aria-labelledby='first-watch-title'] * { font-size: 2rem !important; }",
  });

  const panel = page.getByRole("complementary", { name: "Bosun’s First Watch" });
  await expect(panel).toBeVisible();
  const overflow = await panel.evaluate(
    (element) => element.scrollWidth - element.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  await expect(panel.getByRole("button", { name: "Pause First Watch" })).toBeVisible();
  const start = panel.getByRole("button", { name: "Start First Watch" });
  await expect(start).toBeVisible();
  await expectAxeClean(page);

  await start.click();
  const openCapture = page.getByRole("button", { name: "Open Capture" });
  await openCapture.focus();
  await page.keyboard.press("Space");
  const capture = page.getByRole("dialog", { name: "Quick capture" });
  await expect(capture).toBeVisible();
  await expect(page.locator("main")).toHaveAttribute("inert", "");
  const visibility = page.getByLabel("Who can see this capture");
  await visibility.focus();
  await expect(visibility).toBeFocused();
  await expect(visibility).toBeInViewport();
  await expectAxeClean(page);
  await capture.getByRole("button", { name: "Close" }).click();
  await expect(capture).toBeHidden();
  await expect(page.locator("main")).not.toHaveAttribute("inert", "");
});
