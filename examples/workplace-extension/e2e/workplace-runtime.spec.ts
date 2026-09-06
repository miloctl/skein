import { expect, test, type Browser, type Page } from "@playwright/test";

const IDP = process.env.SKEIN_CONTRACT_IDP_URL || "http://127.0.0.1:8610";
const API = process.env.SKEIN_CONTRACT_API_URL || "http://127.0.0.1:8601";
const APP = process.env.SKEIN_CONTRACT_APP_URL || "http://127.0.0.1:3601";
const SIGN_IN_FAILURES = [
  `response: 401 ${API}/api/attention`,
  `response: 401 ${API}/api/briefing`,
  `response: 401 ${API}/api/whoami`,
  `response: 401 ${API}/api/capabilities?actions=atlas.dashboard.view`,
  `response: 401 ${API}/api/users/theme`,
  "console: Failed to load resource: the server responded with a status of 401 (Unauthorized)",
];

async function signedInPage(
  browser: Browser,
  user: string,
  ignoredFailures: string[] = [],
) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const activeIgnoredFailures = [...SIGN_IN_FAILURES, ...ignoredFailures];
  const failures = watchSignedInPage(page, activeIgnoredFailures);
  await page.route(`${IDP}/authorize**`, async (route) => {
    const url = new URL(route.request().url());
    url.searchParams.set("login_hint", user);
    await route.continue({ url: url.toString() });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeHidden({
    timeout: 15_000,
  });
  await page.waitForURL(
    (url) => url.origin === APP && !url.pathname.startsWith("/auth/"),
    { timeout: 15_000 },
  );
  await page.unroute(`${IDP}/authorize**`);
  const sessionResponse = await context.request.get(`${API}/api/auth/session`);
  expect(sessionResponse.ok()).toBe(true);
  const session = await sessionResponse.json();
  expect(await page.evaluate(() => localStorage.getItem("skein-oidc"))).toBeNull();
  expect(session?.user).toBe(user);
  expect(session?.authenticated).toBe(true);
  expect(session?.csrf_token).toBeTruthy();
  expect(session).not.toHaveProperty("access_token");
  expect(session).not.toHaveProperty("refresh_token");
  await expect(page.getByRole("button", { name: new RegExp(user, "i") })).toBeVisible({
    timeout: 15_000,
  });
  activeIgnoredFailures.splice(0, SIGN_IN_FAILURES.length);
  return { context, page, csrf: String(session.csrf_token), failures };
}

function watchSignedInPage(page: Page, ignoredFailures: string[] = []) {
  const failures: string[] = [];
  page.on("console", (message) => {
    const failure = `console: ${message.text()}`;
    if (message.type() === "error" && !ignoredFailures.includes(failure))
      failures.push(failure);
  });
  page.on("pageerror", (error) => failures.push(`page: ${error.message}`));
  page.on("requestfailed", (request) =>
    failures.push(`request: ${request.method()} ${request.url()}`),
  );
  page.on("response", (response) => {
    const failure = `response: ${response.status()} ${response.url()}`;
    if (response.status() >= 400 && !ignoredFailures.includes(failure))
      failures.push(failure);
  });
  return failures;
}

const authorization = (csrf: string) => ({
  "X-Skein-CSRF": csrf,
  Origin: APP,
});

test("the package-built workplace keeps core writes and extension policy together", async ({
  browser,
}) => {
  const denied = await signedInPage(browser, "ava");
  await denied.page.goto("/dashboard");
  await expect(denied.page.getByRole("link", { name: "Atlas" })).toBeHidden();
  await expect(
    denied.page.getByRole("heading", { name: "Atlas delivery indicators" }),
  ).toHaveCount(0);
  const deniedMetrics = await denied.context.request.get(
    `${API}/api/extensions/atlas.workplace/metrics`,
    { headers: authorization(denied.csrf) },
  );
  expect(deniedMetrics.status()).toBe(403);
  expect(denied.failures).toEqual([]);
  await denied.context.close();

  const integration = await signedInPage(browser, "nina");
  await expect(integration.page.getByRole("link", { name: "Atlas" })).toBeHidden();
  const integrationMetrics = await integration.context.request.get(
    `${API}/api/extensions/atlas.workplace/metrics`,
    { headers: authorization(integration.csrf) },
  );
  expect(integrationMetrics.status()).toBe(403);
  const sync = await integration.context.request.post(`${API}/api/extensions/atlas.workplace/sync`, {
    headers: authorization(integration.csrf),
  });
  expect(sync.status()).toBe(200);
  expect(await sync.json()).toEqual({ created: 0, updated: 0 });
  expect(integration.failures).toEqual([]);
  await integration.context.close();

  const metricsUrl = `${API}/api/extensions/atlas.workplace/metrics`;
  const manager = await signedInPage(browser, "mira", [
    `response: 503 ${metricsUrl}`,
    "console: Failed to load resource: the server responded with a status of 503 (Service Unavailable)",
  ]);
  await manager.page.route(metricsUrl, (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "unavailable" }),
    }),
  );
  await manager.page.goto("/dashboard#atlas-delivery");
  await expect(manager.page.getByRole("link", { name: "Atlas" })).toBeVisible({
    timeout: 15_000,
  });
  await expect(
    manager.page.getByRole("heading", { name: "Atlas delivery indicators" }),
  ).toBeVisible();
  const unavailable = manager.page.getByText(
    "Atlas delivery indicators are unavailable.",
  );
  await expect(unavailable).toBeVisible();
  await expect(unavailable).toHaveAttribute("role", "alert");
  await expect(unavailable).toHaveAttribute("aria-live", "polite");
  await expect(manager.page.getByText("0 linked items · 0 sync runs")).toHaveCount(0);
  await expect(manager.page.getByText("Loading Atlas delivery indicators…")).toHaveCount(0);
  await manager.page.unroute(metricsUrl);
  await manager.page.getByRole("button", { name: "Try again" }).click();
  const recovered = manager.page.getByText("0 linked items · 1 sync run");
  await expect(recovered).toBeVisible();
  await expect(recovered).toHaveAttribute("role", "status");
  await expect(recovered).toHaveAttribute("aria-live", "polite");
  const managerSync = await manager.context.request.post(`${API}/api/extensions/atlas.workplace/sync`, {
    headers: authorization(manager.csrf),
  });
  expect(managerSync.status()).toBe(403);

  await manager.page.getByRole("button", { name: "+ Capture" }).click();
  await manager.page
    .getByRole("textbox", { name: "What to capture" })
    .fill("todo: Prove the package-built Atlas runtime");
  await manager.page.getByRole("button", { name: "Capture", exact: true }).click();
  await expect(manager.page.getByText(/Captured as task #\d+/)).toBeVisible();

  const tasks = await manager.context.request.get(`${API}/api/tasks`, {
    headers: authorization(manager.csrf),
  });
  expect(tasks.status()).toBe(200);
  expect(await tasks.json()).toContainEqual(
    expect.objectContaining({
      title: "Prove the package-built Atlas runtime",
      origin: "human",
      created_by: "mira",
    }),
  );
  expect(manager.failures).toEqual([]);
  await manager.context.close();

  const deniedAgain = await signedInPage(browser, "ava");
  await deniedAgain.page.goto("/dashboard");
  await expect(deniedAgain.page.getByRole("link", { name: "Atlas" })).toBeHidden();
  const deniedMetricsAgain = await deniedAgain.context.request.get(
    `${API}/api/extensions/atlas.workplace/metrics`,
    { headers: authorization(deniedAgain.csrf) },
  );
  expect(deniedMetricsAgain.status()).toBe(403);
  expect(deniedAgain.failures).toEqual([]);
  await deniedAgain.context.close();
});
