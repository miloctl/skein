import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Browser, type Page } from "@playwright/test";

const AVA_KEY = "sk-skein-" + "0".repeat(40);
const MARCUS_KEY = "sk-skein-" + "1".repeat(40);

async function openAs(page: Page, name: string, key: string) {
  await page.goto("/");
  await page.evaluate(
    ([person, apiKey]) => {
      window.localStorage.setItem("skein-user", person);
      window.localStorage.setItem("skein-key", apiKey);
    },
    [name, key],
  );
  await page.goto("/chat");
}

async function privateChatPages(browser: Browser) {
  const avaContext = await browser.newContext();
  const marcusContext = await browser.newContext();
  const ava = await avaContext.newPage();
  const marcus = await marcusContext.newPage();
  await openAs(ava, "ava", AVA_KEY);
  await openAs(marcus, "marcus", MARCUS_KEY);
  return { avaContext, marcusContext, ava, marcus };
}

test("two invited people call two agents and removal revokes access", async ({
  browser,
}) => {
  test.setTimeout(90_000);
  const { avaContext, marcusContext, ava, marcus } = await privateChatPages(browser);
  try {
    await ava.getByRole("button", { name: "New private shared chat" }).click();
    await ava.getByLabel("Private shared chat title").fill("Launch decision");
    await ava.getByRole("button", { name: "Create private shared chat" }).click();
    const avaHeading = ava.getByRole("heading", { name: "Launch decision" });
    await expect(avaHeading).toBeVisible();
    await expect(avaHeading).toBeFocused();

    await ava.getByRole("button", { name: "Manage participants" }).click();
    await ava.getByLabel("Invite a teammate").fill("marcus");
    await ava.getByRole("button", { name: "Review invitation for marcus" }).click();
    await expect(
      ava.getByText("marcus can read every message already in this chat."),
    ).toBeVisible();
    await ava.getByRole("button", { name: "Send invitation to marcus" }).click();

    await marcus.reload();
    await expect(marcus.getByText("Private chat invitation from ava")).toBeVisible();
    await expect(
      marcus.getByText("If you accept, you can read every earlier message in that chat."),
    ).toBeVisible();
    await marcus.getByRole("button", { name: "Accept invitation from ava" }).click();
    const marcusHeading = marcus.getByRole("heading", { name: "Launch decision" });
    await expect(marcusHeading).toBeVisible();
    await expect(marcusHeading).toBeFocused();

    await ava.getByLabel("Message Launch decision").fill("Only invited people can read this.");
    await ava.getByRole("button", { name: "Send message" }).click();
    await marcus.bringToFront();
    await expect(marcus.getByText("Only invited people can read this.")).toBeVisible({
      timeout: 10_000,
    });

    await marcus.getByLabel("Message Launch decision").fill("Marcus received it.");
    await marcus.getByRole("button", { name: "Send message" }).click();
    await ava.bringToFront();
    await expect(ava.getByText("Marcus received it.")).toBeVisible({ timeout: 10_000 });

    await ava.getByLabel("Agent to add").selectOption("backend-architect");
    await ava.getByRole("button", { name: "Review agent access" }).click();
    await expect(
      ava.getByText(
        "Backend Architect can read this private chat history when a participant calls @backend-architect.",
      ),
    ).toBeVisible();
    await expect(
      ava.getByText("The chat history goes to the configured model provider."),
    ).toBeVisible();
    await ava
      .getByRole("button", { name: "Add Backend Architect to this private chat" })
      .click();
    await ava.getByLabel("Agent to add").selectOption("code-reviewer");
    await ava.getByRole("button", { name: "Review agent access" }).click();
    await ava.getByRole("button", { name: "Add Code Reviewer to this private chat" }).click();
    await ava
      .getByRole("button", { name: "Call Backend Architect (@backend-architect)" })
      .click();
    await ava.getByRole("button", { name: "Call Code Reviewer (@code-reviewer)" }).click();
    await ava
      .getByLabel("Message Launch decision")
      .fill("@backend-architect @code-reviewer review the launch boundary");
    await ava.getByRole("button", { name: "Send message" }).click();
    await expect(
      ava.locator("ol").getByText("backend-architect · agent", { exact: false }),
    ).toBeVisible({ timeout: 10_000 });
    await expect(
      ava.locator("ol").getByText("code-reviewer · agent", { exact: false }),
    ).toBeVisible({ timeout: 10_000 });

    await ava.getByRole("button", { name: "Remove marcus" }).click();
    await expect(
      ava.getByText(
        "marcus will lose access to every message in this chat. Their messages stay attributed.",
      ),
    ).toBeVisible();
    await ava.getByRole("button", { name: "Confirm: remove marcus" }).click();
    await marcus.bringToFront();
    await expect(
      marcus.getByText(
        "This private shared chat is no longer available. Select another chat.",
      ),
    ).toBeVisible({ timeout: 10_000 });

    for (const page of [ava, marcus]) {
      const scan = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
        .analyze();
      expect(
        scan.violations.map((violation) => ({
          rule: violation.id,
          nodes: violation.nodes.map((node) => ({
            target: node.target.join(" "),
            html: node.html,
            summary: node.failureSummary,
            checks: [...node.any, ...node.all, ...node.none].map((check) => ({
              id: check.id,
              message: check.message,
              data: check.data,
            })),
          })),
        })),
      ).toEqual([]);
    }
  } finally {
    await avaContext.close();
    await marcusContext.close();
  }
});
