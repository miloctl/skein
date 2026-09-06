import { expect, it } from "vitest";

import config from "../next.config";

it("refuses framing and suppresses MIME guessing and cross-origin referrers", async () => {
  const rules = await config.headers!();
  const rule = rules.find((r) => r.source === "/:path*")!;
  const headers = Object.fromEntries(rule.headers.map(({ key, value }) => [key, value]));
  expect(headers["Content-Security-Policy"]).toContain("frame-ancestors 'none'");
  expect(headers["Content-Security-Policy"]).toContain("img-src 'self' data:");
  expect(headers["X-Content-Type-Options"]).toBe("nosniff");
  expect(headers["Referrer-Policy"]).toBe("same-origin");
});
