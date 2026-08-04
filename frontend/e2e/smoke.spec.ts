import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/** Every walk asserts three things a component test cannot: no console
 *  errors, no failed API requests, and a clean axe scan of the page as the
 *  browser actually composed it. The backend is seeded (seed.py) and runs
 *  the mock provider, so every walk is deterministic and keyless. */

// the five nav destinations plus Settings; paths from components/nav.tsx
const PAGES = [
  { path: "/", name: "My Day" },
  { path: "/chat", name: "Chat" },
  { path: "/portfolio", name: "Work" },
  { path: "/review", name: "Inbox" },
  { path: "/agents", name: "Team" },
  { path: "/settings", name: "Settings" },
];

type Fault = { kind: string; detail: string };

function watch(page: Page): Fault[] {
  const faults: Fault[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") faults.push({ kind: "console", detail: msg.text() });
  });
  page.on("pageerror", (err) => faults.push({ kind: "pageerror", detail: String(err) }));
  page.on("response", (res) => {
    if (res.status() >= 400)
      faults.push({ kind: "request", detail: `${res.status()} ${res.url()}` });
  });
  return faults;
}

async function pickName(page: Page) {
  // trusted-header mode: the X-User name picker is identity. Walks run as a
  // seeded teammate so pages render data, not the anonymous empty states.
  await page.goto("/");
  await page.evaluate(() => window.localStorage.setItem("skein-user", "ava"));
}

for (const { path, name } of PAGES) {
  test(`${name} renders clean and accessible`, async ({ page }) => {
    await pickName(page);
    const faults = watch(page);
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    expect(faults, JSON.stringify(faults, null, 2)).toEqual([]);

    const scan = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa"])
      // the theme cards' miniature previews are pictures of the UI, not
      // information: their glyphs are sample decoration at deliberately
      // reduced opacity, and the card's accessible name carries the label.
      // Contrast rules serve sighted low-vision readers, so aria-hidden
      // does not exempt them — this exclusion, with this reason, does.
      .exclude(".pack-tile")
      .analyze();
    expect(
      scan.violations.map((v) => ({
        rule: v.id,
        impact: v.impact,
        nodes: v.nodes.map((n) => n.target.join(" ")).slice(0, 5),
      })),
    ).toEqual([]);
  });
}

test("a chat turn round-trips through the mock agent", async ({ page }) => {
  await pickName(page);
  const faults = watch(page);
  await page.goto("/chat");
  const composer = page.getByRole("combobox");
  await composer.fill("/help");
  await composer.press("Enter");
  await expect(page.getByText("Mock agent").first()).toBeVisible({ timeout: 15_000 });
  expect(faults, JSON.stringify(faults, null, 2)).toEqual([]);
});
