/** A capture in one tab must refresh another tab's badge before the next
 * 30-second poll. BroadcastChannel carries the local window event across
 * tabs; delivery only reaches OTHER channel objects, so the posting tab
 * cannot double-fire its own listeners. */
import { describe, expect, it } from "vitest";

import { bridgeAttentionChange, notifyAttentionChange } from "@/lib/attention";

// BroadcastChannel delivery lands on its own schedule, not the next task
// tick — one setTimeout(0) loses the race on a loaded machine
async function flush(done: () => boolean = () => true) {
  for (let i = 0; i < 40 && !done(); i++) {
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe("attention change broadcast", () => {
  it("notifies the local tab and posts to the channel", async () => {
    const local: string[] = [];
    const listener = () => local.push("changed");
    window.addEventListener("skein-attention-change", listener);
    const otherTab = new BroadcastChannel("skein-attention");
    const remote: unknown[] = [];
    otherTab.onmessage = (event) => remote.push(event.data);
    try {
      notifyAttentionChange();
      await flush(() => remote.length > 0);
      expect(local).toEqual(["changed"]);
      expect(remote).toEqual(["changed"]);
    } finally {
      window.removeEventListener("skein-attention-change", listener);
      otherTab.close();
    }
  });

  it("relays another tab's change into this tab's window event", async () => {
    const seen: string[] = [];
    const listener = () => seen.push("changed");
    window.addEventListener("skein-attention-change", listener);
    const unbridge = bridgeAttentionChange();
    const otherTab = new BroadcastChannel("skein-attention");
    try {
      otherTab.postMessage("changed");
      await flush(() => seen.length > 0);
      expect(seen).toEqual(["changed"]);

      unbridge();
      // a probe channel proves delivery happened: BroadcastChannel orders
      // messages, so once the probe hears this post, a still-open bridge
      // would already have relayed it — the bare single-tick wait passed
      // even when a broken unbridge simply delivered late
      const probe = new BroadcastChannel("skein-attention");
      const probed: unknown[] = [];
      probe.onmessage = (event) => probed.push(event.data);
      otherTab.postMessage("changed");
      await flush(() => probed.length > 0);
      probe.close();
      expect(seen).toEqual(["changed"]); // a closed bridge relays nothing
    } finally {
      window.removeEventListener("skein-attention-change", listener);
      otherTab.close();
    }
  });
});
