// Cross-tab mirror of the local "skein-attention-change" event: a capture in
// one tab must refresh another tab's badge before the next 30-second poll.
// BroadcastChannel delivers only to OTHER contexts, so the local event and
// the broadcast never double-fire a listener in the posting tab.
const CHANNEL = "skein-attention";

export function notifyAttentionChange() {
  window.dispatchEvent(new Event("skein-attention-change"));
  try {
    const channel = new BroadcastChannel(CHANNEL);
    channel.postMessage("changed");
    channel.close();
  } catch {
    // no BroadcastChannel: the same-tab refresh above still happened
  }
}

export function bridgeAttentionChange(): () => void {
  try {
    const channel = new BroadcastChannel(CHANNEL);
    channel.onmessage = () => window.dispatchEvent(new Event("skein-attention-change"));
    return () => channel.close();
  } catch {
    return () => {};
  }
}
