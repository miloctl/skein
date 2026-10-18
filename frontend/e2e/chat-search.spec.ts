import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const API = process.env.SKEIN_E2E_API_URL ?? "http://127.0.0.1:8600";

// Only a browser lays out the transcript. jsdom has no scroll geometry, so a
// component test cannot see a landed message scroll back out of view when
// assistant-ui follows the bottom on a content resize (Thread's holdLanding).
test("a search hit older than the first page opens with that message focused", async ({
  page,
  request,
}) => {
  // 26 turns are 52 messages, so the hit is past the newest page of 50. The
  // chat cap is 20 turns a minute (backend/app/ratelimit.py), so the seeding
  // waits out its Retry-After once.
  test.setTimeout(150_000);
  for (let n = 0; n < 26; n++) {
    for (;;) {
      const sent = await request.post(`${API}/api/chat`, {
        headers: { "X-User": "ava" },
        data: {
          thread_id: "e2e-chat-search",
          message: n === 0 ? "Bluefinch rotation plan for the staging certificates" : `weekly sync note ${n}`,
        },
      });
      if (sent.status() !== 429) {
        expect(sent.status()).toBe(200);
        break;
      }
      const wait = Number(sent.headers()["retry-after"] ?? "5");
      await new Promise((done) => setTimeout(done, 1000 * wait));
    }
  }
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => window.localStorage.setItem("skein-user", "ava"));
  await page.goto("/chat");

  const box = page.getByRole("searchbox", { name: "Search your chats" });
  await box.fill("LUEFINC");
  await box.press("Enter");
  await expect(page.getByText("1 chat matches.")).toBeVisible();
  const scan = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
  expect(scan.violations).toEqual([]);

  await page.getByRole("button", { name: /Bluefinch rotation plan/ }).click();

  const landed = page.locator("[data-message-id]:focus");
  await expect(landed).toContainText("Bluefinch rotation plan");
  await expect(landed).toBeInViewport();
  // a late content resize must not scroll it back to the bottom (Thread's holdLanding)
  await page.waitForLoadState("networkidle");
  await expect(landed).toBeInViewport();
  await expect(page.getByRole("textbox", { name: /Message/ })).not.toBeFocused();
  await expect(page.getByText(/more than 1,000 messages back/)).toHaveCount(0);
});
