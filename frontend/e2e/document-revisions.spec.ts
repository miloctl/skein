import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

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

const AVA_KEY = "sk-skein-" + "0".repeat(40);
const MARCUS_KEY = "sk-skein-" + "1".repeat(40);

async function signIn(page: Page, name: string, key: string) {
  await page.goto("/settings");
  await page.getByLabel("Personal API key", { exact: true }).fill(key);
  await page.getByRole("button", { name: "Sign in with key", exact: true }).click();
  await expect(page.getByText(`● strong identity active as ${name}`)).toBeVisible();
}

test("write a private document that only its author can open", async ({ browser }) => {
  const avaContext = await browser.newContext();
  const marcusContext = await browser.newContext();
  try {
    const ava = await avaContext.newPage();
    await signIn(ava, "ava", AVA_KEY);
    await ava.goto("/artifacts");
    await ava.getByRole("button", { name: "New document", exact: true }).click();
    // a signed-in person starts at "only you" (routes/api.py::_personal_default)
    await ava.getByLabel("Title", { exact: true }).fill("Cutover checklist");
    await ava.getByLabel("Document text, Markdown").fill("# Cutover\n\nStep one.\n");
    await ava.getByRole("button", { name: "Create document", exact: true }).click();
    await expect(ava.getByRole("status").filter({ hasText: /Created document #\d+\./ })).toBeVisible();
    await expect(ava.locator("p", { hasText: "Step one." })).toBeVisible();
    await expect(ava.getByText("only you", { exact: true })).toBeVisible();
    const id = new URL(ava.url()).searchParams.get("id");
    expect(id).toMatch(/^\d+$/);
    const scan = await new AxeBuilder({ page: ava })
      .withTags(["wcag2a", "wcag2aa", "wcag22aa"])
      .analyze();
    expect(scan.violations.map((v) => ({ id: v.id, nodes: v.nodes.map((n) => n.target) }))).toEqual([]);

    const marcus = await marcusContext.newPage();
    await signIn(marcus, "marcus", MARCUS_KEY);
    await marcus.goto(`/artifacts?id=${id}`);
    await expect(marcus.getByText(/no artifact #\d+|not found/i).first()).toBeVisible();
    await expect(marcus.getByText("Step one.")).toHaveCount(0);
  } finally {
    await avaContext.close();
    await marcusContext.close();
  }
});
