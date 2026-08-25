export type SharedChatSummary = {
  id: string;
  title: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  engagement_id: number | null;
  role: "steward" | "member";
  member_count: number;
  unread_count: number;
};

type SharedChatMember = {
  person: string;
  role: "steward" | "member";
  joined_at: string;
  kind?: "agent";
  // human members only — agents carry no cursor
  last_read_message_id?: number;
};

export type SharedChatInvitation = {
  id: number;
  invited_by: string;
  created_at: string;
};

type PendingSharedChatInvitation = SharedChatInvitation & {
  person: string;
};

export type SharedChatDetail = {
  id: string;
  kind: "shared";
  title: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  engagement_id: number | null;
  engagement_name: string;
  viewer: string;
  role: "steward" | "member";
  members: SharedChatMember[];
  pending_invitations: PendingSharedChatInvitation[];
};

export type SharedChatMessage = {
  id: number;
  thread_id: string;
  role: "user" | "assistant";
  author_kind: "legacy" | "human" | "agent" | "system";
  author: string;
  content: string;
  created_at: string;
  turn_id: string;
  reply_to_message_id: number | null;
  deleted_at: string | null;
};

export type SharedChatAgentRun = {
  turn_id: string;
  batch_id: string;
  trigger_message_id: number;
  response_message_id: number | null;
  agent: string;
  requested_by: string;
  status: "pending" | "running" | "completed" | "refused" | "failed" | "completion_unknown";
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
  error_code: string;
};

export type BenchPersona = {
  slug: string;
  name: string;
  description: string;
  emoji: string;
  vibe: string;
  disclosure: string;
};

export function announceSharedChatActivity() {
  window.dispatchEvent(new Event("skein-shared-chat-activity"));
}

// Same-tab writers dispatch a synthetic new Event("storage") for ANY
// localStorage change — the sidebar toggle (lib/chat-layout.ts), theme
// adoption (lib/theme.ts), the manage toggle. Acting on that form wiped the
// open private room on a sidebar click. Only a real cross-tab StorageEvent
// carries a key; same-tab identity changes arrive as skein-identity-change
// (lib/api.ts, lib/auth.ts). A null key on a real event is storage.clear().
export function isIdentityEvent(event: Event): boolean {
  if (event.type !== "storage") return true;
  if (!(event instanceof StorageEvent)) return false;
  return !event.key || ["skein-user", "skein-key", "skein-oidc"].includes(event.key);
}
