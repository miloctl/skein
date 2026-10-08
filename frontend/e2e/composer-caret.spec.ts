import { expect, test } from "@playwright/test";

// The composer is a controlled textarea whose value comes from the chat store.
// A React state update inside onChange commits before the store carries the
// new text, writes the old text back, and parks the caret at the end — so an
// edit in the middle of a draft lands every later character at the end.
// Only a browser reproduces this: jsdom does not re-run React's controlled
// value restore against a live caret.
test("typing in the middle of a draft keeps the caret there", async ({ page }) => {
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => window.localStorage.setItem("skein-user", "ava"));
  await page.goto("/chat");
  const composer = page.getByRole("textbox", { name: /Message/ });
  await composer.click();
  await page.keyboard.type("hello world");
  await page.keyboard.press("Home");
  await page.keyboard.type("AB");
  await composer.evaluate((el: HTMLTextAreaElement) => el.setSelectionRange(5, 5));
  await page.keyboard.type("CD");
  await expect(composer).toHaveValue("ABhelCDlo world");
  expect(await composer.evaluate((el: HTMLTextAreaElement) => el.selectionStart)).toBe(7);
});
