"use client";

import { Card as ExtensionCard } from "@miloctl/skein-extension-api";
import type { ComponentProps } from "react";

export { EmptyState } from "@miloctl/skein-extension-api";

export function Card({
  className = "",
  titleClassName = "",
  ...props
}: ComponentProps<typeof ExtensionCard>) {
  return (
    <ExtensionCard
      {...props}
      className={`skein-card ${className}`}
      titleClassName={`skein-section-title ${titleClassName}`}
    />
  );
}
