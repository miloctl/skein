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
