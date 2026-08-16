import { describe, expect, it } from "vitest";

import { completeSignIn } from "@/lib/auth";

describe("sign-in callback errors", () => {
  it("does not reflect the identity provider query value", async () => {
    const canary = "SECRETVALUE-from-query";
    let message = "";
    try {
      await completeSignIn(`?error=${encodeURIComponent(canary)}`);
    } catch (error) {
      message = error instanceof Error ? error.message : String(error);
    }

    expect(message).toBe(
      "The identity provider refused the sign-in. Start the sign-in again.",
    );
    expect(message).not.toContain(canary);
  });
});
