"use client";

import {
  ThreadPrimitive,
  MessagePrimitive,
  ComposerPrimitive,
} from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import remarkGfm from "remark-gfm";

const MarkdownText = () => (
  <MarkdownTextPrimitive
    remarkPlugins={[remarkGfm]}
    className="prose-chat break-words"
  />
);

const UserMessage = () => (
  <MessagePrimitive.Root className="flex justify-end py-2">
    <div className="max-w-[80%] rounded-2xl bg-thread-solid px-4 py-2.5 text-sm text-white">
      <MessagePrimitive.Parts />
    </div>
  </MessagePrimitive.Root>
);

const AssistantMessage = () => (
  <MessagePrimitive.Root className="flex justify-start py-2">
    <div className="max-w-[85%] rounded-2xl bg-raised px-4 py-2.5 text-sm text-ink">
      <MessagePrimitive.Parts components={{ Text: MarkdownText }} />
    </div>
  </MessagePrimitive.Root>
);

const Composer = () => (
  <ComposerPrimitive.Root className="flex items-end gap-2 rounded-xl border border-line-strong bg-card p-2 shadow-card">
    <ComposerPrimitive.Input
      autoFocus
      placeholder="Message the Chief of Staff… (/help for commands, or just ask)"
      className="max-h-40 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-ink-3"
      rows={1}
    />
    <ComposerPrimitive.Send className="rounded-lg bg-thread-solid px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-40">
      Send
    </ComposerPrimitive.Send>
  </ComposerPrimitive.Root>
);

export function Thread() {
  return (
    <ThreadPrimitive.Root className="flex h-full flex-col">
      <ThreadPrimitive.Viewport className="flex-1 overflow-y-auto px-4 pt-4">
        <ThreadPrimitive.Empty>
          <div className="mx-auto mt-16 max-w-md text-center text-ink-3">
            <p className="text-lg font-semibold text-ink">
              Skein Chief of Staff
            </p>
            <p className="mt-2 text-sm">
              Track milestones, log questions, record decisions, post standups,
              and plan projects — just ask. Try: <em>“Plan a launch for our new
              onboarding flow”</em> or <em>“What&apos;s on the calendar this week?”</em>
            </p>
            <p className="mt-3 text-xs text-ink-3">
              Type <code>/help</code> for commands — <code>/plan</code>,{" "}
              <code>/playbooks</code>, <code>/search</code>, <code>/briefing</code>,{" "}
              <code>/remember</code>. Anything else is smart-captured.
            </p>
          </div>
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages
          components={{ UserMessage, AssistantMessage }}
        />
        <ThreadPrimitive.ViewportFooter className="sticky bottom-0 bg-gradient-to-t from-page via-page to-transparent pb-4 pt-2 ">
          <Composer />
        </ThreadPrimitive.ViewportFooter>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
}
