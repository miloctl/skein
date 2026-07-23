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
    <div className="max-w-[80%] rounded-2xl bg-indigo-600 px-4 py-2.5 text-sm text-white">
      <MessagePrimitive.Parts />
    </div>
  </MessagePrimitive.Root>
);

const AssistantMessage = () => (
  <MessagePrimitive.Root className="flex justify-start py-2">
    <div className="max-w-[85%] rounded-2xl bg-zinc-100 px-4 py-2.5 text-sm text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100">
      <MessagePrimitive.Parts components={{ Text: MarkdownText }} />
    </div>
  </MessagePrimitive.Root>
);

const Composer = () => (
  <ComposerPrimitive.Root className="flex items-end gap-2 rounded-xl border border-zinc-300 bg-white p-2 shadow-sm dark:border-zinc-700 dark:bg-zinc-900">
    <ComposerPrimitive.Input
      autoFocus
      placeholder="Message the Chief of Staff… (e.g. “plan our Q3 launch”, “what's blocked?”)"
      className="max-h-40 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-zinc-400"
      rows={1}
    />
    <ComposerPrimitive.Send className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 disabled:opacity-40">
      Send
    </ComposerPrimitive.Send>
  </ComposerPrimitive.Root>
);

export function Thread() {
  return (
    <ThreadPrimitive.Root className="flex h-full flex-col">
      <ThreadPrimitive.Viewport className="flex-1 overflow-y-auto px-4 pt-4">
        <ThreadPrimitive.Empty>
          <div className="mx-auto mt-16 max-w-md text-center text-zinc-500">
            <p className="text-lg font-semibold text-zinc-700 dark:text-zinc-200">
              Strands Chief of Staff
            </p>
            <p className="mt-2 text-sm">
              Track milestones, log questions, record decisions, post standups,
              and plan projects — just ask. Try: <em>“Plan a launch for our new
              onboarding flow”</em> or <em>“What&apos;s on the calendar this week?”</em>
            </p>
          </div>
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages
          components={{ UserMessage, AssistantMessage }}
        />
        <ThreadPrimitive.ViewportFooter className="sticky bottom-0 bg-gradient-to-t from-white via-white to-transparent pb-4 pt-2 dark:from-zinc-950 dark:via-zinc-950">
          <Composer />
        </ThreadPrimitive.ViewportFooter>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
}
