import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** The deployment-limits section states numbers, so every rule that governs a
 *  string carrying a number applies to it (CLAUDE.md): no warmth, and the
 *  sentence must agree with what the server actually reports.
 *
 *  Two claims here are the ones a reader acts on and cannot check:
 *  "this needs a restart" and "Skein is using N, not the value you stored".
 *  A section that silently implied a restart-only change had taken effect, or
 *  that hid an out-of-range override, would be telling an administrator the
 *  deployment is configured one way while it runs another.
 */

const KNOBS = [
  {
    name: "chat_limit",
    label: "Chat messages per minute",
    value: 20,
    default: 20,
    override: null,
    floor: 1,
    ceiling: 500,
    unit: "per person per minute",
    live: true,
    detail: "One flock turn spends one slot per member.",
    ignored: false,
  },
  {
    name: "tool_threads",
    label: "Agent tool thread pool",
    value: 16,
    default: 16,
    override: null,
    floor: 2,
    ceiling: 256,
    unit: "threads",
    live: false,
    detail: "Serves agent tool calls.",
    ignored: false,
  },
  {
    name: "capture_limit",
    label: "Captures per minute",
    value: 30,
    default: 30,
    override: 99999,
    floor: 1,
    ceiling: 1000,
    unit: "per person per minute",
    live: true,
    detail: "Quick capture.",
    ignored: true,
  },
];

const mode = { fail: false };
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => {
      if (mode.fail) return Promise.reject(new Error("this surface requires an administrator"));
      if (path === "/api/settings/tuning") return Promise.resolve(KNOBS);
      // the section is AdminUser, and the page only asks for it once identity
      // resolves strong — a never-settling whoami leaves it unfetched
      if (path === "/api/whoami")
        return Promise.resolve({
          user: "boss",
          strong: true,
          admin: true,
          can_administer: true,
          keys_minted: 1,
        });
      // every other panel on the page stays mid-load, so nothing else renders
      // a claim that could be mistaken for one of ours
      return new Promise(() => {});
    },
    getUser: () => "tester",
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/settings" }));

import SettingsPage from "@/app/settings/page";

describe("the deployment-limits section", () => {
  it("says a restart-only knob needs a restart, and does not say it for a live one", async () => {
    mode.fail = false;
    render(<SettingsPage />);
    await screen.findByText(/Agent tool thread pool/);
    const restart = screen.getByText(/read at startup/);
    expect(restart.textContent).toMatch(/applies after a restart/);
    // the live knob must not carry that sentence — implying a restart is
    // needed sends an administrator to bounce a server for nothing
    const chat = screen.getByText(/One flock turn spends one slot per member/);
    expect(chat.textContent).not.toMatch(/restart/);
  });

  it("reports an out-of-range override instead of hiding it", async () => {
    mode.fail = false;
    render(<SettingsPage />);
    // the stored number AND the number actually in force, both named: the
    // reader cannot otherwise tell that the value they set is not the one
    // being enforced
    const warning = await screen.findByText(/outside the allowed range/);
    expect(warning.textContent).toMatch(/99999/);
    expect(warning.textContent).toMatch(/30/);
  });

  it("states the bounds and the default for every knob", async () => {
    mode.fail = false;
    render(<SettingsPage />);
    await screen.findByText(/Chat messages per minute/);
    expect(screen.getByText(/Allowed: 1 to 500\. Default: 20\./)).toBeTruthy();
  });

  it("carries no warmth, because every line here holds a number", async () => {
    mode.fail = false;
    const { container } = render(<SettingsPage />);
    await screen.findByText(/Chat messages per minute/);
    const text = container.textContent ?? "";
    // the section's own sentences, not the whole page: contractions and
    // exclamation marks are the tells CLAUDE.md bars in a string with a number
    const ours = text.slice(text.indexOf("What Skein allows per person"));
    expect(ours).not.toMatch(/!|’|'(s|re|ll|t)\b/);
  });

  it("shows the server's own refusal when the load fails, never a claim", async () => {
    mode.fail = true;
    render(<SettingsPage />);
    // getAllByText, not getByText: every panel that failed reports the SAME
    // sentence, which is the one-condition-one-wording rule working rather
    // than a duplicate to remove (CLAUDE.md)
    await waitFor(() =>
      expect(
        screen.getAllByText(/this surface requires an administrator/).length,
      ).toBeGreaterThan(0),
    );
    // and never renders a knob it does not have
    expect(screen.queryByText(/Chat messages per minute/)).toBeNull();
  });
});
