"use client";

import { RuntimeProvider } from "./runtime-provider";
import { Thread } from "@/components/thread";

export default function ChatPage() {
  return (
    <RuntimeProvider>
      <main className="mx-auto flex h-[calc(100vh-3.5rem)] w-full max-w-3xl flex-col">
        <Thread />
      </main>
    </RuntimeProvider>
  );
}
