import { expect, test, type Page } from "@playwright/test";

const API = process.env.SKEIN_OIDC_API_URL ?? "http://127.0.0.1:8601";

async function signIn(page: Page) {
  await page.goto("/");
  const origin = new URL(page.url()).origin;
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  const exchanged = page.waitForResponse((r) => r.url().endsWith("/api/auth/token") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Sign in" }).click();
  const response = await exchanged;
  expect(response.ok()).toBe(true);
  const metadata = await response.json();
  expect(metadata.authenticated).toBe(true);
  expect(metadata).not.toHaveProperty("access_token");
  expect(metadata).not.toHaveProperty("refresh_token");
  expect(metadata).not.toHaveProperty("key");
  await page.waitForURL((url) => url.origin === origin && !url.pathname.startsWith("/auth/"), { timeout: 15_000 });
  await expect(page.getByRole("button", { name: /ava/i }).first()).toBeVisible({ timeout: 15_000 });
}

test("a signed-out visitor is gated and a metadata-only exchange opens the workspace", async ({ page }) => {
  const errors: string[] = [];
  await signIn(page);
  const stored = await page.evaluate(() => ({ oidc: localStorage.getItem("skein-oidc"), key: localStorage.getItem("skein-key") }));
  expect(stored).toEqual({ oidc: null, key: null });
  const cookie = (await page.context().cookies(API)).find((c) => c.name === "__Host-skein-session");
  expect(cookie).toMatchObject({ httpOnly: true, secure: true, sameSite: "Lax", path: "/" });
  expect(await page.evaluate(() => document.cookie)).not.toContain("__Host-skein-session");
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  await page.getByRole("link", { name: "Work", exact: true }).click();
  await expect(page.locator("main")).toBeVisible();
  expect(errors).toEqual([]);
});

test("authenticated reads carry cookie-bound CSRF and no bearer or name header", async ({ page }) => {
  await signIn(page);
  const authorized = page.waitForRequest((r) => r.url().includes("/api/") && Boolean(r.headers()["x-skein-csrf"]));
  await page.getByRole("link", { name: "Work", exact: true }).click();
  const request = await authorized;
  expect(request.headers()["x-skein-csrf"]).toBeTruthy();
  expect(request.headers()["authorization"]).toBeUndefined();
  expect(request.headers()["x-user"]).toBeUndefined();
});

test("a callback that did not start in this tab is refused", async ({ page }) => {
  await page.goto("/auth/callback?code=code-forged&state=not-this-tab");
  await expect(page.locator("body")).toContainText(/did not start in this tab|no longer valid/i, { timeout: 15_000 });
  expect((await page.context().cookies(API)).some((c) => c.name === "__Host-skein-session")).toBe(false);
});

test("sign-out revokes the browser cookie and clears another tab's private page", async ({ page }) => {
  await signIn(page);
  const other = await page.context().newPage();
  await other.goto("/people");
  await expect(other.getByRole("button", { name: /ava/i }).first()).toBeVisible();
  await page.bringToFront();
  await page.getByRole("button", { name: /ava/i }).first().click();
  const revoked = page.waitForResponse((r) => r.url().endsWith("/api/auth/session") && r.request().method() === "DELETE");
  await page.getByRole("menuitem", { name: "Sign out", exact: true }).click();
  expect((await revoked).status()).toBe(204);
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible({ timeout: 15_000 });
  await other.bringToFront();
  await expect(other.getByRole("button", { name: "Sign in" })).toBeVisible({ timeout: 15_000 });
  expect((await page.context().cookies(API)).some((c) => c.name === "__Host-skein-session")).toBe(false);
  await other.close();
});
