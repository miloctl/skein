import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/** The half of the app the smoke walks never saw: every page, at phone width,
 *  and in dark. smoke.spec.ts covers six pages, light, at one desktop width,
 *  plus /portfolio in dark — so a defect that only appears at 360px or only in
 *  dark could not be caught by anything here. Four of those shipped at once:
 *  a `<select>` with no name, a `<p>` inside a `<ul>`, three keyboard-
 *  unreachable scroll regions, and a header that wrapped to a third row and
 *  pushed the chat composer off-screen.
 *
 *  Structured as WALKS, not one test per page: the suite's stated shape is "a
 *  handful of walks", and a per-page test at four combinations would be 56 of
 *  them. Each walk visits every page and reports every problem it found at
 *  once, so one run tells you the whole story instead of the first failure. */

const PAGES = [
  "/",
  "/chat",
  "/portfolio",
  "/dashboard",
  "/insights",
  "/review",
  "/intake",
  "/ingest",
  "/agents",
  "/people",
  "/charter",
  "/activity",
  "/guide",
  "/settings",
];

// 22 characters, which is an ordinary name — deps.py caps at 64. The header
// bug was invisible against the 3-character seeded user, so the phone walks
// carry the long one: content extremes are where layout invariants break.
const LONG_NAME = "annamaria-vandenberghe";

type Problem = { page: string; what: string; detail: string };

/** Everything measurable about one rendered page. Kept in the browser as one
 *  evaluate so a walk costs one round trip per page. */
async function probe(page: Page) {
  return page.evaluate(() => {
    const de = document.documentElement;
    const header = document.querySelector("header");
    const selvage = document.querySelector(".selvage");
    const navH =
      parseFloat(getComputedStyle(de).getPropertyValue("--nav-h")) * 16;
    // a deliberate scroll container is not overflow — the nav's own scroller,
    // the review table, the flock diagram. Only their ANCESTORS would be.
    const scrollers = new Set(
      [...document.querySelectorAll("*")].filter((el) =>
        /auto|scroll/.test(getComputedStyle(el).overflowX),
      ),
    );
    const inScroller = (el: Element) => {
      for (let p = el.parentElement; p; p = p.parentElement)
        if (scrollers.has(p)) return true;
      return false;
    };
    return {
      // if this ever reads ANON the walk is measuring a different app than it
      // claims: identity lives in localStorage, and a silent miss made a whole
      // 56-combination audit worthless once
      anonymous: (header?.innerText ?? "").includes("anonymous"),
      overflowPx: de.scrollWidth - de.clientWidth,
      overflowCulprit:
        [...document.querySelectorAll("*")]
          .filter((el) => !inScroller(el) && !scrollers.has(el))
          .map((el) => ({ el, r: el.getBoundingClientRect() }))
          .filter(({ r }) => r.width > 0 && r.right > de.clientWidth + 1)
          .map(
            ({ el }) => `${el.tagName}.${String(el.className).slice(0, 60)}`,
          )[0] ?? "",
      // globals.css publishes --nav-h and /chat sizes itself from it; drift
      // means the chat composer sits below the fold by exactly this much
      headerDrift: header
        ? Math.round(
            header.getBoundingClientRect().height -
              (navH + (selvage ? selvage.getBoundingClientRect().height : 0)),
          )
        : 0,
      coarse: matchMedia("(pointer: coarse)").matches,
      // globals.css sets a 16px floor for coarse pointers because iOS Safari
      // zooms on focus below it and stays zoomed
      smallInputs: [
        ...document.querySelectorAll("input,textarea,select,[contenteditable]"),
      ]
        .filter((el) => (el as HTMLElement).offsetParent)
        .map((el) => parseFloat(getComputedStyle(el).fontSize))
        .filter((size) => size < 16).length,
    };
  });
}

function walk(label: string, opts: { phone: boolean; dark: boolean }) {
  test(label, async ({ page }) => {
    test.setTimeout(180_000);
    const problems: Problem[] = [];
    page.on("pageerror", (err) =>
      problems.push({
        page: page.url(),
        what: "pageerror",
        detail: String(err).slice(0, 160),
      }),
    );
    await page.goto("/");
    await page.evaluate(
      (n) => window.localStorage.setItem("skein-user", n),
      opts.phone ? LONG_NAME : "ava",
    );

    for (const path of PAGES) {
      await page.goto(path);
      await page.waitForLoadState("networkidle").catch(() => {});
      const p = await probe(page);
      const add = (what: string, detail: string) =>
        problems.push({ page: path, what, detail });

      if (p.anonymous)
        add(
          "identity",
          "rendered the anonymous path — the walk proves nothing",
        );
      if (p.overflowPx > 1)
        add("overflow", `${p.overflowPx}px sideways · ${p.overflowCulprit}`);
      if (Math.abs(p.headerDrift) > 1)
        add("header", `${p.headerDrift}px off --nav-h`);
      if (opts.phone && !p.coarse)
        add("harness", "pointer is not coarse — the 16px floor cannot apply");
      if (p.coarse && p.smallInputs)
        add("inputs", `${p.smallInputs} under 16px on a touch pointer`);

      const scan = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag22aa"])
        // the tiles preview OTHER packs on purpose — same reason smoke.spec.ts gives
        .exclude(".pack-tile")
        .analyze();
      for (const v of scan.violations)
        add("axe", `${v.impact} ${v.id} ×${v.nodes.length}`);
    }
    expect(problems, JSON.stringify(problems, null, 2)).toEqual([]);
  });
}

test.describe("phone", () => {
  test.use({
    viewport: { width: 360, height: 800 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  });
  walk("every page holds at 360px, light", { phone: true, dark: false });
  test.describe("dark", () => {
    test.use({ colorScheme: "dark" });
    walk("every page holds at 360px, dark", { phone: true, dark: true });
  });
});

test.describe("desktop", () => {
  test.use({ viewport: { width: 1280, height: 1400 } });
  walk("every page holds at 1280px, light", { phone: false, dark: false });
  test.describe("dark", () => {
    test.use({ colorScheme: "dark" });
    walk("every page holds at 1280px, dark", { phone: false, dark: true });
  });
});
