import { expect, test } from "@playwright/test";

/** The oidc sign-in flow in a real browser, against a signing stub IdP
 *  (scripts/stub-idp.py). oidc is the production auth mode, and every other
 *  suite walks trusted-header or api-key — without this walk, the mode the
 *  deployment actually uses is the one nothing renders.
 *
 *  Smoke depth: the round trip, what it stores, and the two refusals that
 *  make the round trip safe. The unit tests in __tests__/auth-ladder.test.ts
 *  and backend/tests/test_auth_modes.py hold the detail. */

test("a signed-out visitor is gated, and the round trip opens the workspace", async ({
  page,
}) => {
  const errors: string[] = [];
  await page.goto("/");
  const appOrigin = new URL(page.url()).origin;
  // the gate, not the workspace: no name picker in oidc mode
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await expect(page.getByText("Sign in to open the workspace.")).toBeVisible();

  await page.getByRole("button", { name: "Sign in" }).click();

  // the browser really leaves for the IdP and really comes back
  await expect(page.getByRole("button", { name: "Sign in" })).toBeHidden({
    timeout: 15_000,
  });
  await page.waitForURL(
    (url) => url.origin === appOrigin && !url.pathname.startsWith("/auth/"),
    { timeout: 15_000 },
  );

  // the workspace renders under the IdP's identity, and the name comes from
  // the validated token rather than anything the browser asserted
  await expect(page.locator("body")).toContainText(/ava/i, { timeout: 15_000 });

  const stored = await page.evaluate(() =>
    JSON.parse(window.localStorage.getItem("skein-oidc") || "null"),
  );
  expect(stored?.access_token).toBeTruthy();
  expect(stored?.user).toBe("ava");

  // Listening starts only once the session exists. Before that a signed-out
  // visitor holds no token and the reads behind the gate answer 401 by
  // design, and the sign-in transition still has some in flight — counting
  // those would assert that being signed out is an error. What must be
  // clean is the SIGNED-IN app, so navigate once and read the console then.
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  await page.getByRole("link", { name: "Work" }).click();
  await expect(page.locator("main")).toBeVisible();
  expect(errors).toEqual([]);
});

test("an authenticated request carries the bearer token, not a name header", async ({
  page,
}) => {
  await page.goto("/");
  const appOrigin = new URL(page.url()).origin;
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeHidden({
    timeout: 15_000,
  });
  // the callback page redirects to returnTo once the exchange lands; a
  // navigation issued before that settles is cancelled by it
  await page.waitForURL(
    (url) => url.origin === appOrigin && !url.pathname.startsWith("/auth/"),
    { timeout: 15_000 },
  );

  const authorized = page.waitForRequest(
    (r) => r.url().includes("/api/") && !!r.headers()["authorization"],
    { timeout: 15_000 },
  );
  await page.getByRole("link", { name: "Work" }).click();
  const request = await authorized;
  expect(request.headers()["authorization"]).toMatch(/^Bearer /);
});

test("a callback that did not start in this tab is refused", async ({ page }) => {
  // the state check is what makes a link someone else crafted useless: it
  // can carry a code, but not the random value this tab generated
  await page.goto("/auth/callback?code=code-forged&state=not-this-tab");
  await expect(page.locator("body")).toContainText(
    /did not start in this tab|no longer valid/i,
    { timeout: 15_000 },
  );
  const stored = await page.evaluate(() =>
    window.localStorage.getItem("skein-oidc"),
  );
  expect(stored).toBeNull();
});

test("signing out clears the stored session", async ({ page }) => {
  await page.goto("/");
  const appOrigin = new URL(page.url()).origin;
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeHidden({
    timeout: 15_000,
  });
  // the callback's redirect is still in flight here, and it destroys the
  // execution context an evaluate() runs in
  await page.waitForURL(
    (url) => url.origin === appOrigin && !url.pathname.startsWith("/auth/"),
    { timeout: 15_000 },
  );
  expect(
    await page.evaluate(() => window.localStorage.getItem("skein-oidc")),
  ).toBeTruthy();

  await page.getByRole("button", { name: /ava/i }).first().click();
  const signedOut = page.waitForNavigation({
    waitUntil: "domcontentloaded",
    timeout: 15_000,
  });
  await page.getByText("Sign out").click();
  await signedOut;

  // The reload commits the cleared session before this context reads storage.
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible({
    timeout: 15_000,
  });
  expect(
    await page.evaluate(() => window.localStorage.getItem("skein-oidc")),
  ).toBeNull();
});
