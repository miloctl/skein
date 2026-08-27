import { expect, test, type Browser, type Page } from "@playwright/test";

const IDP = "http://127.0.0.1:8610";
const API = "http://127.0.0.1:8601";

async function signedInPage(browser: Browser, user: string) {
  const context = await browser.newContext();
  const page = await context.newPage();
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
  await page.waitForURL((url) => !url.pathname.startsWith("/auth/"), {
    timeout: 15_000,
  });
  await page.unroute(`${IDP}/authorize**`);
  const session = await page.evaluate(() =>
    JSON.parse(window.localStorage.getItem("skein-oidc") || "null"),
  );
  expect(session?.user).toBe(user);
  expect(session?.access_token).toBeTruthy();
  return { context, page, token: String(session.access_token) };
}

function watchSignedInPage(page: Page) {
  const failures: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") failures.push(`console: ${message.text()}`);
  });
  page.on("pageerror", (error) => failures.push(`page: ${error.message}`));
  page.on("requestfailed", (request) =>
    failures.push(`request: ${request.method()} ${request.url()}`),
  );
  page.on("response", (response) => {
    if (response.status() >= 400)
      failures.push(`response: ${response.status()} ${response.url()}`);
  });
  return failures;
}

const authorization = (token: string) => ({
  Authorization: `Bearer ${token}`,
});

test("the package-built workplace keeps core writes and extension policy together", async ({
  browser,
  request,
}) => {
  const denied = await signedInPage(browser, "ava");
  await denied.page.goto("/dashboard");
  await expect(denied.page.getByRole("link", { name: "Atlas" })).toBeHidden();
  await expect(
    denied.page.getByRole("heading", { name: "Atlas delivery indicators" }),
  ).toHaveCount(0);
  const deniedMetrics = await request.get(
    `${API}/api/extensions/atlas.workplace/metrics`,
    { headers: authorization(denied.token) },
  );
  expect(deniedMetrics.status()).toBe(403);
  await denied.context.close();

  const integration = await signedInPage(browser, "nina");
  await expect(integration.page.getByRole("link", { name: "Atlas" })).toBeHidden();
  const integrationMetrics = await request.get(
    `${API}/api/extensions/atlas.workplace/metrics`,
    { headers: authorization(integration.token) },
  );
  expect(integrationMetrics.status()).toBe(403);
  const sync = await request.post(`${API}/api/extensions/atlas.workplace/sync`, {
    headers: authorization(integration.token),
  });
  expect(sync.status()).toBe(200);
  expect(await sync.json()).toEqual({ created: 0, updated: 0 });
  await integration.context.close();

  const manager = await signedInPage(browser, "mira");
  const failures = watchSignedInPage(manager.page);
  await manager.page.goto("/dashboard#atlas-delivery");
  await expect(manager.page.getByRole("link", { name: "Atlas" })).toBeVisible();
  await expect(
    manager.page.getByRole("heading", { name: "Atlas delivery indicators" }),
  ).toBeVisible();
  await expect(manager.page.getByText("0 linked items · 1 sync runs")).toBeVisible();
  const managerSync = await request.post(`${API}/api/extensions/atlas.workplace/sync`, {
    headers: authorization(manager.token),
  });
  expect(managerSync.status()).toBe(403);

  await manager.page.getByRole("button", { name: "+ Capture" }).click();
  await manager.page
    .getByRole("textbox", { name: "What to capture" })
    .fill("todo: Prove the package-built workplace runtime");
  await manager.page.getByRole("button", { name: "Capture", exact: true }).click();
  await expect(manager.page.getByText(/Captured as task #\d+/)).toBeVisible();

  const tasks = await request.get(`${API}/api/tasks`, {
    headers: authorization(manager.token),
  });
  expect(tasks.status()).toBe(200);
  expect(await tasks.json()).toContainEqual(
    expect.objectContaining({
      title: "Prove the package-built workplace runtime",
      origin: "human",
      created_by: "mira",
    }),
  );
  expect(failures).toEqual([]);
  await manager.context.close();

  const deniedAgain = await signedInPage(browser, "ava");
  await deniedAgain.page.goto("/dashboard");
  await expect(deniedAgain.page.getByRole("link", { name: "Atlas" })).toBeHidden();
  const deniedMetricsAgain = await request.get(
    `${API}/api/extensions/atlas.workplace/metrics`,
    { headers: authorization(deniedAgain.token) },
  );
  expect(deniedMetricsAgain.status()).toBe(403);
  await deniedAgain.context.close();
});
