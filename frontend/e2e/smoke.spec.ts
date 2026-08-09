import { readFile } from "node:fs/promises";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/** Every walk asserts three things a component test cannot: no console
 *  errors, no failed API requests, and a clean axe scan of the page as the
 *  browser actually composed it. The backend is seeded (seed.py) and runs
 *  the mock provider, so every walk is deterministic and keyless. */

// the nav destinations plus Settings and the Reports tab; paths from
// components/nav.tsx and components/section-tabs.tsx
const PAGES = [
  { path: "/", name: "My Day" },
  { path: "/chat", name: "Chat" },
  { path: "/portfolio", name: "Work" },
  // its own walk: the reader renders markdown the backend generators wrote,
  // so a broken parse shows as a clean-but-empty page that no fetch failure
  // would report
  { path: "/artifacts", name: "Reports" },
  { path: "/review", name: "Inbox" },
  { path: "/agents", name: "Team" },
  { path: "/settings", name: "Settings" },
];

type Fault = { kind: string; detail: string };

function watch(page: Page): Fault[] {
  const faults: Fault[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error")
      faults.push({ kind: "console", detail: msg.text() });
  });
  page.on("pageerror", (err) =>
    faults.push({ kind: "pageerror", detail: String(err) }),
  );
  page.on("response", (res) => {
    if (res.status() >= 400)
      faults.push({ kind: "request", detail: `${res.status()} ${res.url()}` });
  });
  return faults;
}

async function pickName(page: Page, key = "") {
  // trusted-header mode: the X-User name picker is identity. Walks run as a
  // seeded teammate so pages render data, not the anonymous empty states.
  await page.goto("/");
  await page.evaluate(
    ([name, apiKey]) => {
      window.localStorage.setItem("skein-user", name);
      // a key is STRONG identity, and several surfaces render nothing without
      // one — the Crews card's whole interactive half, and the deployment
      // limits. A walk with no key scans an empty read-only page and reports
      // it clean.
      if (apiKey) window.localStorage.setItem("skein-key", apiKey);
      else window.localStorage.removeItem("skein-key");
    },
    ["ava", key],
  );
}

for (const { path, name } of PAGES) {
  test(`${name} renders clean and accessible`, async ({ page }) => {
    await pickName(page);
    const faults = watch(page);
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    expect(faults, JSON.stringify(faults, null, 2)).toEqual([]);

    const scan = await new AxeBuilder({ page })
      // 2.1 and 2.2 too: the 2.0-only tag set skips target-size, focus
      // appearance, and the reflow rules this app is built to hold
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
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

test("dark mode holds the same bar", async ({ page }) => {
  // the light-mode walks above never see dark tokens: text-3 on raised
  // shipped at 4.16:1 in dark with every light scan green. One dense page
  // in dark keeps that class visible to the browser, not only to the
  // token-level gate in scripts/check_theme_contrast.py.
  await pickName(page);
  await page.emulateMedia({ colorScheme: "dark" });
  const faults = watch(page);
  await page.goto("/portfolio");
  await page.waitForLoadState("networkidle");
  expect(faults, JSON.stringify(faults, null, 2)).toEqual([]);
  const scan = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa"])
    .exclude(".pack-tile")
    .analyze();
  expect(
    scan.violations.map((v) => ({ rule: v.id, impact: v.impact })),
  ).toEqual([]);
});

test("a chat turn round-trips through the mock agent", async ({ page }) => {
  await pickName(page);
  const faults = watch(page);
  await page.goto("/chat");
  const composer = page.getByRole("combobox");
  await composer.fill("/help");
  await composer.press("Enter");
  await expect(page.getByText("Mock agent").first()).toBeVisible({
    timeout: 15_000,
  });
  expect(faults, JSON.stringify(faults, null, 2)).toEqual([]);
});

test("the crews card is operable with a keyboard and announces what it did", async ({
  page,
}) => {
  // The gap this closes: every other walk sets only skein-user, so the card
  // renders read-only and CI has never seen its interactive half at all.
  const key = (await readFile("/tmp/skein-e2e/ava.key", "utf8")).match(
    /sk-skein-\S+/,
  )?.[0];
  expect(
    key,
    "seed must mint a key for the strong-identity walks",
  ).toBeTruthy();
  await pickName(page, key);

  const faults = watch(page);
  await page.goto("/settings");
  await page.waitForLoadState("networkidle");

  const platform = page.getByRole("button", {
    name: "Remove marcus from Platform",
  });
  await expect(platform).toBeVisible();

  // opening the confirm must MOVE focus and CHANGE the accessible name, or a
  // screen reader hears the same string twice and no state change
  await platform.focus();
  await page.keyboard.press("Enter");
  const confirm = page.getByRole("button", {
    name: "Confirm: remove marcus from Platform",
  });
  await expect(confirm).toBeFocused();

  // Escape cancels and hands focus back to the trigger it came from
  await page.keyboard.press("Escape");
  await expect(platform).toBeFocused();

  // and the write announces itself through the shared live region
  await page
    .getByLabel("Add someone to Platform")
    .fill("annamaria-vandenberghe");
  await page.getByRole("button", { name: "Add to Platform" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "added to Platform" }),
  ).toBeVisible();

  expect(faults, JSON.stringify(faults, null, 2)).toEqual([]);
  const scan = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .exclude(".pack-tile")
    .analyze();
  expect(
    scan.violations.map((v) => ({ rule: v.id, impact: v.impact })),
  ).toEqual([]);
});
