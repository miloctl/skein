import { afterEach, describe, expect, it, vi } from "vitest";

import { copyText } from "@/lib/clipboard";

/** Skein can be served over plain http, where navigator.clipboard does not
 *  exist. The fallback chain is the contract: clipboard API when present,
 *  the hidden-textarea path when absent OR when the API rejects (permission
 *  denied, focus lost), false only when both fail. jsdom has neither API,
 *  so each rung is stubbed explicitly. */

afterEach(() => {
  vi.unstubAllGlobals();
  // @ts-expect-error jsdom has no execCommand; tests install and remove it
  delete document.execCommand;
});

describe("copyText fallback chain", () => {
  it("uses navigator.clipboard when it exists and reports success", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    await expect(copyText("hello")).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith("hello");
  });

  it("falls back to the textarea path when the clipboard API rejects", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    document.execCommand = vi.fn().mockReturnValue(true);
    await expect(copyText("fallback me")).resolves.toBe(true);
    expect(document.execCommand).toHaveBeenCalledWith("copy");
    // the hidden textarea must not survive the copy
    expect(document.querySelector("textarea")).toBeNull();
  });

  it("uses the textarea path directly when there is no clipboard API", async () => {
    vi.stubGlobal("navigator", {});
    document.execCommand = vi.fn().mockImplementation(() => {
      // the legacy API copies the current selection: the value must already
      // be in the document and selected when execCommand fires
      const ta = document.querySelector("textarea");
      expect(ta?.value).toBe("plain http");
      return true;
    });
    await expect(copyText("plain http")).resolves.toBe(true);
  });

  it("restores focus and removes the textarea when the fallback returns false", async () => {
    vi.stubGlobal("navigator", {});
    document.execCommand = vi.fn().mockReturnValue(false);
    const button = document.createElement("button");
    document.body.appendChild(button);
    button.focus();

    await expect(copyText("nope")).resolves.toBe(false);
    expect(document.querySelector("textarea")).toBeNull();
    expect(document.activeElement).toBe(button);
    button.remove();
  });

  it("restores focus and removes the textarea when the fallback throws", async () => {
    vi.stubGlobal("navigator", {});
    document.execCommand = vi.fn().mockImplementation(() => {
      throw new Error("unsupported");
    });
    const button = document.createElement("button");
    document.body.appendChild(button);
    button.focus();

    await expect(copyText("nope")).resolves.toBe(false);
    expect(document.querySelector("textarea")).toBeNull();
    expect(document.activeElement).toBe(button);
    button.remove();
  });
});
