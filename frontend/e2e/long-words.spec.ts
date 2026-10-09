import { expect, test } from "@playwright/test";

// A title is one long token often enough (a pasted id, a URL). The 360px walk
// in responsive.spec.ts holds on seeded data, which has none; these rows are
// what pushed My Day and Approvals sideways on a phone.
test("one long token in a title does not widen a phone page", async ({ browser }) => {
  test.setTimeout(120_000);
  const ctx = await browser.newContext({ viewport: { width: 360, height: 740 } });
  const page = await ctx.newPage();
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => window.localStorage.setItem("skein-user", "ava"));
  const token = "longtoken".repeat(24);
  await page.goto("/");
  await page.getByRole("button", { name: "+ Capture" }).click();
  const box = page.getByRole("textbox", { name: "What to capture" });
  await box.fill(`todo: ${token}`);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("status").filter({ hasText: "Captured as task" })).toBeVisible();
  await page.keyboard.press("Escape");
  // an agent proposal carries the long token into the Approvals payload table
  await page.goto("/chat");
  const composer = page.getByRole("textbox", { name: /Message/ });
  await composer.fill("/as growth-mentor");
  await composer.press("Enter");
  await page.waitForTimeout(1500);
  await composer.fill(`please file ${token}`);
  await composer.press("Enter");
  await page.waitForTimeout(3000);
  const overflows = async () =>
    page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  for (const route of ["/", "/review"]) {
    await page.goto(route);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(800);
    expect(await overflows(), `${route} scrolls sideways`).toBeLessThanOrEqual(0);
  }
  await ctx.close();
});
