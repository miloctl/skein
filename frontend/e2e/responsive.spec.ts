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
  // fonts BEFORE geometry: a pack re-cuts the type, and until its webfont
  // arrives the header is measured in fallback metrics. That briefly wraps it
  // to three rows, which read as a 56px --nav-h drift in phosphor and hermes —
  // a defect in the measurement, not in the app.
  await page.evaluate(() => document.fonts.ready);
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

/** The header is sticky, so a target scrolled to the top of the viewport lands
 *  under it. axe cannot see this — it is a scroll position, not markup — and
 *  the skip link was the worst case, putting #content 110px under the header
 *  for the one control that exists to reach the content. globals.css answers
 *  with scroll-margin-top on :target and [tabindex="-1"]. */
for (const vw of [360, 1280]) {
  test(`the skip link clears the sticky header at ${vw}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: vw, height: 800 });
    await page.goto("/");
    await page.evaluate(() => window.localStorage.setItem("skein-user", "ava"));
    await page.goto("/dashboard");
    await page.waitForLoadState("networkidle").catch(() => {});
    // from far down the page: the bug only shows when following the link
    // actually has to scroll
    await page.evaluate(() => window.scrollTo(0, 1500));
    await page.keyboard.press("Tab");
    await expect(page.locator(":focus")).toHaveText(/Skip to content/);
    await page.keyboard.press("Enter");
    await page.waitForTimeout(300);
    const { headerBottom, mainTop } = await page.evaluate(() => ({
      headerBottom: document.querySelector("header")!.getBoundingClientRect()
        .bottom,
      mainTop: document.getElementById("content")!.getBoundingClientRect().top,
    }));
    expect(
      Math.round(mainTop),
      `#content sits ${Math.round(headerBottom - mainTop)}px under the header`,
    ).toBeGreaterThanOrEqual(Math.round(headerBottom));
  });
}

/** A LAYOUT test, deliberately not a contrast one: check_theme_contrast.py
 *  already sweeps 7 packs x 6 colorways x 3 surfaces x both modes, plus all
 *  360 custom hues, on every lint run — a browser adds nothing there. What it
 *  cannot see is reflow. Phosphor and Atelier bump --fs-xs from 12px to 13px
 *  and are the widest text in the app; Ledger, Phosphor and Hermes square
 *  every radius and carry different selvage heights, which feed the --nav-h
 *  arithmetic the header depends on. */
test("every fabric pack reflows at 360px without breaking the page", async ({
  page,
}) => {
  test.setTimeout(180_000);
  await page.setViewportSize({ width: 360, height: 800 });
  const problems: Problem[] = [];
  // a pack re-cuts the type, so a font that fails to load is measured in
  // fallback metrics — which is a wider header, not a missing one
  page.on("response", (r) => {
    if (r.status() >= 400)
      problems.push({
        page: new URL(r.url()).pathname,
        what: "request",
        detail: String(r.status()),
      });
  });
  await page.goto("/");
  await page.evaluate(
    (n) => window.localStorage.setItem("skein-user", n),
    LONG_NAME,
  );
  // densest, longest, and the one page that sizes itself from --nav-h
  for (const path of ["/dashboard", "/settings", "/chat"]) {
    for (const pack of [
      "loom",
      "ledger",
      "phosphor",
      "contrast",
      "atelier",
      "claw",
      "hermes",
    ]) {
      await page.goto(path);
      await page.evaluate(
        (p) => window.localStorage.setItem("skein-pack", p),
        pack,
      );
      await page.reload();
      await page.waitForLoadState("networkidle").catch(() => {});
      // wait for HYDRATION, not a timeout: the name comes from localStorage
      // through useSyncExternalStore, so until it renders the header is a
      // different width than the one being measured — which reported a 56px
      // --nav-h drift for phosphor and hermes against an app that was fine.
      await page
        .waitForFunction(
          (n) => document.querySelector("header")?.innerText.includes(n),
          LONG_NAME,
          { timeout: 10_000 },
        )
        .catch(() => {});
      const p = await probe(page);
      const applied = await page.evaluate(
        () => document.documentElement.dataset.pack ?? "loom",
      );
      const where = `${path} · ${pack}`;
      // a pack that did not apply makes every measurement below a measurement
      // of Loom, reported as a pass for the pack
      if (applied !== pack)
        problems.push({
          page: where,
          what: "pack",
          detail: `rendered ${applied}`,
        });
      if (p.overflowPx > 1)
        problems.push({
          page: where,
          what: "overflow",
          detail: `${p.overflowPx}px · ${p.overflowCulprit}`,
        });
      if (Math.abs(p.headerDrift) > 1) {
        const shape = await page.evaluate(() => {
          const h = document.querySelector("header")!;
          const row = h.querySelector("div")!;
          return {
            h: Math.round(h.getBoundingClientRect().height),
            rows: new Set(
              [...row.children].map((e) =>
                Math.round(e.getBoundingClientRect().top),
              ),
            ).size,
            nav: h.innerText.replace(/\n/g, "|").slice(0, 40),
            vw: window.innerWidth,
          };
        });
        problems.push({
          page: where,
          what: "header",
          detail: `${p.headerDrift}px off --nav-h · ${JSON.stringify(shape)}`,
        });
      }
    }
  }
  await page.evaluate(() => window.localStorage.removeItem("skein-pack"));
  expect(problems, JSON.stringify(problems, null, 2)).toEqual([]);
});

/** The three answers a screen owes: loading, empty, error — and never two at
 *  once. The repo treats a false empty state as its most expensive defect
 *  (__tests__/agents-silent-catches.test.tsx, false-claims.test.tsx), but
 *  those mock at the api() layer in jsdom. This forces the states in a real
 *  browser, where the page is composed. lib/api.ts::backendUnreachable is the
 *  one wording every surface must use, so it doubles as the assertion. */
const CLAIMS: Record<string, RegExp> = {
  "/agents":
    /No agent identities yet|No rules yet|Nothing remembered yet|No flock has flown yet/,
  "/review": /propose changes, they wait here/,
  "/intake": /No requests yet/,
  "/charter": /No charter entries yet/,
  "/activity": /Nothing on the ledger yet/,
};

test("a dead backend says so, and claims nothing", async ({ page }) => {
  test.setTimeout(180_000);
  const problems: Problem[] = [];
  await page.goto("/");
  await page.evaluate(() => window.localStorage.setItem("skein-user", "ava"));
  // every API call fails at the transport layer — the `isUnreachable` branch
  await page.route("**/api/**", (route) => route.abort("failed"));

  for (const [path, claim] of Object.entries(CLAIMS)) {
    await page.goto(path);
    await page.waitForLoadState("networkidle").catch(() => {});
    const text = await page.evaluate(() => document.body.innerText);
    if (!text.includes("Check that the server is running"))
      problems.push({
        page: path,
        what: "silent",
        detail: "no unreachable sentence",
      });
    const claimed = text.match(claim);
    if (claimed)
      problems.push({
        page: path,
        what: "false claim",
        detail: `said "${claimed[0]}" with no data`,
      });
  }
  await page.unroute("**/api/**");
  expect(problems, JSON.stringify(problems, null, 2)).toEqual([]);
});
