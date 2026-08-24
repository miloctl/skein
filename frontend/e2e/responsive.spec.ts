import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/** The half of the app the smoke walks never saw: every page, at phone width,
 *  and in dark. smoke.spec.ts covers seven pages, light, at one desktop width,
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
  "/artifacts",
  "/planning",
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
    const navHToken = getComputedStyle(de).getPropertyValue("--nav-h").trim();
    if (!navHToken)
      throw new Error(
        "Skein CSS did not load. Check the production build and asset responses.",
      );
    const navH = parseFloat(navHToken) * 16;
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

/** The name is HIDDEN below `sm` and must still be announced. At 360px the
 *  header holds the logo, the search field, the identity chip and the capture
 *  button in 328px, and the name at 7rem left the search field 10px wide — so
 *  the chip shows the avatar alone and carries the name as sr-only text. Drop
 *  that text instead of hiding it and the button announces "You" to a screen
 *  reader, with no way to tell which identity is in force. */
test("search results stay inside a 360px viewport", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await page.route("**/api/search?*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          entity: "note",
          entity_id: 7,
          title: "Vendor call",
          snippet: "Follow-up",
        },
      ]),
    }),
  );
  await page.goto("/");
  await page.evaluate(() => window.localStorage.setItem("skein-user", "ava"));
  await page.reload();

  const search = page.getByLabel("Search Skein");
  await search.fill("vendor");
  await search.press("Enter");
  const results = page.getByRole("region", { name: "Search results" });
  await expect(results).toBeVisible();
  const box = await results.boundingBox();

  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(360);
});

test("page help stays reachable in a narrow, short viewport", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 320 });
  await page.goto("/review");
  await page.evaluate(() => window.localStorage.setItem("skein-user", "ava"));
  await page.reload();

  const trigger = page.getByRole("button", { name: "Help for this page" });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Help for this page" });
  await expect(dialog).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Close page help" }),
  ).toBeFocused();

  const box = await dialog.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(320);
  expect(box!.y + box!.height).toBeLessThanOrEqual(320);

  await dialog.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  await expect(
    page.getByRole("link", { name: "Open the field guide" }),
  ).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("the identity chip announces who you are at phone width", async ({
  page,
}) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto("/");
  await page.evaluate(() => window.localStorage.setItem("skein-user", "ava"));
  await page.goto("/");
  await page.evaluate(() => document.fonts.ready);
  const chip = page.getByRole("button", { name: /ava/ });
  await expect(chip).toBeAttached();
  // visually hidden, not display:none — a name the browser does not render is
  // also a name it does not expose
  const shown = await page
    .locator("header span", { hasText: /^ava$/ })
    .first()
    .evaluate((el) => getComputedStyle(el).display !== "none");
  expect(shown).toBe(true);
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

for (const vw of [360, 1280]) {
  test(`settings section links clear the sticky header at ${vw}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: vw, height: 800 });
    await page.goto("/");
    await page.evaluate(() => window.localStorage.setItem("skein-user", "ava"));
    await page.goto("/settings");

    const nav = page.getByRole("navigation", { name: "Settings sections" });
    await expect(nav).toBeVisible();
    expect(await nav.evaluate((el) => getComputedStyle(el).position)).toBe(
      vw >= 1024 ? "sticky" : "static",
    );

    for (const [name, id] of [
      ["You", "settings-you"],
      ["Connections", "settings-connections"],
      ["AI runtime", "settings-ai-runtime"],
      ["Team", "settings-team"],
    ]) {
      await nav.getByRole("link", { name, exact: true }).click();
      await page.waitForFunction(
        (target) => location.hash === `#${target}`,
        id,
      );
      const position = await page.evaluate((target) => {
        const header = document
          .querySelector("header")!
          .getBoundingClientRect();
        const section = document
          .getElementById(target)!
          .getBoundingClientRect();
        return { headerBottom: header.bottom, sectionTop: section.top };
      }, id);
      expect(
        Math.round(position.sectionTop),
        `#${id} sits under the sticky header`,
      ).toBeGreaterThanOrEqual(Math.round(position.headerBottom));
      await expect(nav.getByRole("link", { name, exact: true })).toHaveAttribute(
        "aria-current",
        "location",
      );
    }

    await page.evaluate(() =>
      document.getElementById("settings-connections")!.scrollIntoView(),
    );
    await expect(
      nav.getByRole("link", { name: "Team", exact: true }),
    ).toHaveAttribute("aria-current", "location");
    await expect(
      nav.getByRole("link", { name: "Connections", exact: true }),
    ).not.toHaveAttribute("aria-current");
    expect(await page.evaluate(() => location.hash)).toBe("#settings-team");

    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth -
          document.documentElement.clientWidth,
      ),
    ).toBeLessThanOrEqual(1);
  });
}

test("a direct Settings section hash survives hydration", async ({ page }) => {
  const faults: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") faults.push(message.text());
  });
  page.on("pageerror", (error) => faults.push(error.message));
  await page.goto("/");
  await page.evaluate(() => window.localStorage.setItem("skein-user", "ava"));

  await page.goto("/settings#settings-team");

  await expect(
    page.getByRole("region", { name: "Team", exact: true }),
  ).toBeVisible();
  await expect(page.locator("#settings-you")).toBeHidden();
  await expect(
    page
      .getByRole("navigation", { name: "Settings sections" })
      .getByRole("link", { name: "Team", exact: true }),
  ).toHaveAttribute("aria-current", "location");
  expect(faults, JSON.stringify(faults, null, 2)).toEqual([]);
});

test("changed forms reflow at 320px", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto("/");
  await page.evaluate(
    (name) => window.localStorage.setItem("skein-user", name),
    LONG_NAME,
  );

  for (const path of ["/dashboard", "/intake", "/settings"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle").catch(() => {});
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      ),
      `${path} overflows at 320px`,
    ).toBeLessThanOrEqual(1);
  }
});

test("changed inline actions meet the 24px target minimum", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto("/");
  await page.evaluate(
    (name) => window.localStorage.setItem("skein-user", name),
    LONG_NAME,
  );

  for (const path of ["/planning", "/dashboard"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle").catch(() => {});
    const scan = await new AxeBuilder({ page }).withRules(["target-size"]).analyze();
    expect(
      scan.violations.map((violation) => ({
        page: path,
        nodes: violation.nodes.map((node) => ({
          target: node.target.join(" "),
          summary: node.failureSummary,
        })),
      })),
    ).toEqual([]);
  }
});

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

/** Every colorway re-dyes --thread and --weld, and the -solid fill halves
 *  derived from them carry white text on the destructive and approving
 *  buttons. check_theme_contrast.py proves the RATIOS; this proves the tokens
 *  actually resolve per colorway in a browser, which a stylesheet parse
 *  cannot — a mistyped hex in one colorway block computes to nothing and the
 *  button renders transparent with white text on the page beneath. */
test("every colorway resolves its fill tokens", async ({ page }) => {
  test.setTimeout(120_000);
  const problems: Problem[] = [];
  await page.goto("/");
  await page.evaluate(() => window.localStorage.setItem("skein-user", "ava"));
  for (const way of [
    "indigo",
    "madder",
    "verdigris",
    "graphite",
    "coral",
    "bone",
  ]) {
    for (const appearance of ["light", "dark"]) {
      await page.emulateMedia({ colorScheme: appearance as "light" | "dark" });
      await page.evaluate(
        (w) => window.localStorage.setItem("skein-theme", w),
        way,
      );
      await page.reload();
      await page.waitForLoadState("networkidle").catch(() => {});
      const bad = await page.evaluate(() => {
        const cs = getComputedStyle(document.documentElement);
        return [
          "--thread",
          "--thread-solid",
          "--weld",
          "--weld-solid",
          "--ok-solid",
          "--danger-solid",
        ].filter((t) => !cs.getPropertyValue(t).trim());
      });
      for (const token of bad)
        problems.push({
          page: `${way}/${appearance}`,
          what: "token",
          detail: `${token} resolves to nothing`,
        });
    }
  }
  await page.emulateMedia({ colorScheme: "light" });
  await page.evaluate(() => window.localStorage.removeItem("skein-theme"));
  expect(problems, JSON.stringify(problems, null, 2)).toEqual([]);
});

/** Content extremes beyond the long name: the header bug hid behind a
 *  3-character user, and these are the other inputs a real workspace grows. */
test("extreme content does not break the shell", async ({ page }) => {
  test.setTimeout(120_000);
  const problems: Problem[] = [];
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto("/");
  await page.evaluate(
    (n) => window.localStorage.setItem("skein-user", n),
    LONG_NAME,
  );
  // a four-digit verdict count in the nav badge, which is tabular-nums inside
  // a rounded-full pill sized by padding alone
  await page.route("**/api/attention", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      // `inbox` is what the nav badge renders; `count` and `yours` are what
      // the tab title and the CLI read (services/briefing.py::attention_count).
      // The badge is the four-digit case this test exists for.
      body: '{"count":3,"yours":3,"inbox":9999}',
    }),
  );
  await page.goto("/dashboard");
  await page.waitForLoadState("networkidle").catch(() => {});
  await page
    .waitForFunction(
      (n) => document.querySelector("header")?.innerText.includes(n),
      LONG_NAME,
      { timeout: 10_000 },
    )
    .catch(() => {});
  const p = await probe(page);
  if (p.overflowPx > 1)
    problems.push({
      page: "/dashboard",
      what: "overflow",
      detail: `${p.overflowPx}px · ${p.overflowCulprit}`,
    });
  if (Math.abs(p.headerDrift) > 1)
    problems.push({
      page: "/dashboard",
      what: "header",
      detail: `${p.headerDrift}px off --nav-h`,
    });
  const badge = await page.evaluate(() =>
    document.body.innerText.includes("9999"),
  );
  if (!badge)
    problems.push({
      page: "/dashboard",
      what: "badge",
      detail: "9999 never rendered",
    });
  await page.unroute("**/api/attention");
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

/** Loading, and TRUE empty — the two states the dead-backend walk cannot
 *  reach. Empty needs a SHAPED body, not []: __tests__/no-raw-payloads.test.tsx
 *  records that returning a bare array where an object is expected makes a
 *  page render nothing at all, which passes an "is the claim absent" check for
 *  entirely the wrong reason. */
test("a page that is still loading claims nothing yet", async ({ page }) => {
  test.setTimeout(120_000);
  const problems: Problem[] = [];
  await page.goto("/");
  await page.evaluate(() => window.localStorage.setItem("skein-user", "ava"));
  // hold every API call open forever: the page is permanently mid-load
  await page.route("**/api/**", () => {});

  for (const [path, claim] of Object.entries(CLAIMS)) {
    await page.goto(path).catch(() => {});
    await page.waitForTimeout(600);
    const text = await page.evaluate(() => document.body.innerText);
    const claimed = text.match(claim);
    if (claimed)
      problems.push({
        page: path,
        what: "false claim",
        detail: `said "${claimed[0]}" before the answer arrived`,
      });
    // and it must not report a failure that has not happened
    if (text.includes("Check that the server is running"))
      problems.push({
        page: path,
        what: "premature error",
        detail: "unreachable while loading",
      });
  }
  await page.unroute("**/api/**");
  expect(problems, JSON.stringify(problems, null, 2)).toEqual([]);
});

test("a truly empty workspace says so without breaking", async ({ page }) => {
  test.setTimeout(120_000);
  const problems: Problem[] = [];
  page.on("pageerror", (e) =>
    problems.push({
      page: page.url(),
      what: "pageerror",
      detail: String(e).slice(0, 160),
    }),
  );
  await page.goto("/");
  await page.evaluate(() => window.localStorage.setItem("skein-user", "ava"));
  // shaped-empty, never []: a list endpoint answers [], an object endpoint
  // answers its own keys emptied
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const OBJECTS: Record<string, unknown> = {
      "/api/attention": { count: 0 },
      "/api/agents/entities": { entities: [], always_review: [] },
      "/api/agents/status": {
        provider: "mock",
        model: "m",
        provider_error: "",
        review_gate: false,
        context_strategy: "sliding",
        context_error: "",
      },
      "/api/whoami": { user: "ava", strong: false, keys_minted: 0 },
      "/api/users/growth-interests": { interests: "" },
      "/api/field-guide/hint": { tied_count: 0, total: 0 },
      "/api/auth/config": { mode: "trusted-header" },
      // an OBJECT endpoint, and the reason this map exists: answering [] here
      // made /activity throw on f.entries. Copy the real shape, never []
      // (__tests__/no-raw-payloads.test.tsx records the same trap).
      "/api/activity/feed": { entries: [], next_before: null },
    };
    const body = path in OBJECTS ? OBJECTS[path] : [];
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });

  for (const path of Object.keys(CLAIMS)) {
    await page.goto(path);
    await page.waitForLoadState("networkidle").catch(() => {});
    const text = await page.evaluate(() => document.body.innerText);
    // an empty workspace is allowed to claim emptiness — what it may NOT do
    // is render a machine payload or an error it did not receive
    if (/\[object Object\]|\bundefined\b|\bNaN\b/.test(text))
      problems.push({
        page: path,
        what: "raw payload",
        detail: text.slice(0, 80),
      });
    if (text.includes("Check that the server is running"))
      problems.push({
        page: path,
        what: "false error",
        detail: "unreachable on a 200",
      });
  }
  await page.unroute("**/api/**");
  expect(problems, JSON.stringify(problems, null, 2)).toEqual([]);
});

/** Focus rings, walked with REAL Tab presses — el.focus() does not match
 *  :focus-visible in Chromium, so a programmatic walk reports every element as
 *  ringless and proves nothing. Scoped deliberately: globals.css sets one
 *  global 2px outline and check_theme_contrast.py already holds --thread to
 *  >=5.5:1 on every pack surface, so the COLOUR is settled. What is not
 *  settled is whether an overflow ancestor eats the ring — /settings has
 *  .pack-tile (overflow-hidden) and /agents has the flock diagram's
 *  overflow-x-auto box. */
for (const path of ["/settings", "/agents"]) {
  test(`no focus ring is clipped on ${path}`, async ({ page }) => {
    test.setTimeout(120_000);
    const problems: Problem[] = [];
    await page.goto("/");
    await page.evaluate(() => window.localStorage.setItem("skein-user", "ava"));
    await page.goto(path);
    await page.waitForLoadState("networkidle").catch(() => {});

    const seen = new Set<string>();
    for (let i = 0; i < 80; i++) {
      await page.keyboard.press("Tab");
      const stop = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement | null;
        if (!el || el === document.body) return null;
        const cs = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        const pad =
          parseFloat(cs.outlineWidth || "0") +
          parseFloat(cs.outlineOffset || "0");
        let clippedBy = "";
        for (let p = el.parentElement; p; p = p.parentElement) {
          const pc = getComputedStyle(p);
          if (
            !/hidden|auto|scroll|clip/.test(
              pc.overflow + pc.overflowX + pc.overflowY,
            )
          )
            continue;
          const pr = p.getBoundingClientRect();
          // only the NEAREST clipping ancestor decides
          if (
            r.left - pad < pr.left - 0.5 ||
            r.right + pad > pr.right + 0.5 ||
            r.top - pad < pr.top - 0.5
          )
            clippedBy = String(p.className).slice(0, 40);
          break;
        }
        return {
          id: (el.textContent || el.getAttribute("aria-label") || el.tagName)
            .trim()
            .slice(0, 30),
          ring: cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) >= 2,
          clippedBy,
        };
      });
      if (!stop) break; // wrapped back out of the document
      if (seen.has(stop.id + stop.clippedBy)) continue;
      seen.add(stop.id + stop.clippedBy);
      if (!stop.ring)
        problems.push({ page: path, what: "no ring", detail: stop.id });
      if (stop.clippedBy)
        problems.push({
          page: path,
          what: "clipped ring",
          detail: `${stop.id} by .${stop.clippedBy}`,
        });
    }
    expect(
      seen.size,
      "tabbed nowhere — the walk proves nothing",
    ).toBeGreaterThan(5);
    expect(problems, JSON.stringify(problems, null, 2)).toEqual([]);
  });
}

test.describe("Guided First Week", () => {
  test.use({
    viewport: { width: 360, height: 800 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  });

  test("keeps setup first and the team disclosure operable at phone widths", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    await page.goto("/");
    await page.evaluate(() =>
      window.localStorage.setItem("skein-user", "guided-browser-user"),
    );

    for (const width of [360, 390]) {
      await page.setViewportSize({ width, height: 800 });
      await page.evaluate(() =>
        window.localStorage.removeItem("skein-onboarded:guided-browser-user"),
      );
      await page.goto("/");
      await page.waitForLoadState("networkidle").catch(() => {});

      const setup = page.getByRole("heading", {
        level: 2,
        name: /Your first-week setup/,
      });
      const needs = page.getByRole("heading", { level: 2, name: "Needs you" });
      const work = page.getByRole("heading", { level: 2, name: "Your work" });
      const toggle = page.getByRole("button", { name: /Show team context/ });
      await expect(setup).toBeVisible();
      await expect(toggle).toHaveText(/Show team context \(\d+ items?\)/);

      const [setupBox, needsBox, workBox, toggleBox] = await Promise.all([
        setup.boundingBox(),
        needs.boundingBox(),
        work.boundingBox(),
        toggle.boundingBox(),
      ]);
      expect(setupBox?.y).toBeLessThan(needsBox!.y);
      expect(needsBox?.y).toBeLessThan(workBox!.y);
      expect(workBox?.y).toBeLessThan(toggleBox!.y);

      await toggle.focus();
      await page.keyboard.press("Enter");
      await expect(
        page.getByRole("button", { name: /Hide team context/ }),
      ).toBeFocused();
      await expect(
        page.getByRole("heading", { name: "Team today" }),
      ).toBeVisible();
      await expect(
        page.getByRole("heading", { name: "Since yesterday" }),
      ).toBeVisible();

      const scan = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag22aa"])
        .analyze();
      expect(
        scan.violations.map((v) => ({ rule: v.id, impact: v.impact })),
      ).toEqual([]);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth - innerWidth,
        ),
      ).toBeLessThanOrEqual(1);

      await page.keyboard.press("Enter");
      await expect(
        page.getByRole("heading", { name: "Since yesterday" }),
      ).toHaveCount(0);

      const dismiss = page.getByRole("button", {
        name: "Dismiss first-week setup",
      });
      await dismiss.focus();
      await page.keyboard.press("Enter");
      await expect(page.locator("main#content")).toBeFocused();
    }
  });
});
