import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/** A person edits the seeded agent document, and restores the revision an
 *  agent wrote. Every save is a revision, so the restore keeps both. */
for (const width of [360, 1280]) {
  test(`edit and restore a document at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await page.evaluate(() => localStorage.setItem("skein-user", "ava"));
    await page.goto("/artifacts");
    await page.getByRole("button", { name: /Pricing research plan/ }).click();
    await expect(page.getByText("Compare the six competitor pricing pages.")).toBeVisible();

    await page.getByRole("button", { name: "Edit", exact: true }).click();
    const text = page.getByLabel("Document text, Markdown");
    await expect(text).toHaveValue(/Compare the six/);
    const suffix = `Checked by ava at ${width}px.`;
    await text.fill(`# Pricing research plan\n\n${suffix}\n`);
    await page.getByRole("button", { name: "Preview", exact: true }).click();
    await expect(page.locator("p", { hasText: suffix })).toBeVisible();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("status").filter({ hasText: /Saved revision \d+\./ })).toBeVisible();
    await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeFocused();
    await expect(page.locator("p", { hasText: suffix })).toBeVisible();

    await page.getByRole("button", { name: "History", exact: true }).click();
    await expect(page.getByText(/ava · Person/).first()).toBeVisible();
    await page.getByRole("button", { name: "Restore revision 1", exact: true }).click();
    await expect(
      page.getByRole("status").filter({ hasText: /Restored revision 1 as revision \d+\./ }),
    ).toBeVisible();
    await page.getByRole("button", { name: "History", exact: true }).click();
    await expect(page.locator("p", { hasText: "Compare the six competitor pricing pages." })).toBeVisible();

    expect(
      await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
    ).toBeLessThanOrEqual(1);
    const scan = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
    expect(scan.violations.map((v) => ({ id: v.id, nodes: v.nodes.map((n) => n.target) }))).toEqual([]);
  });
}
