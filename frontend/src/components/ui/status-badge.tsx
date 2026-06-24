"use client";

import { cn } from "@/lib/utils";

type AgentStatus = "running" | "stopped" | "building" | "crashed" | "idle";

const STATUS_CONFIG: Record<
  AgentStatus,
  { bg: string; text: string; dotClass: string }
> = {
  running: {
    bg: "bg-[var(--color-success-bg)]",
    text: "text-[var(--color-success-text)]",
    dotClass: "bg-[var(--semantic-green-400)] status-dot-pulse",
  },
  stopped: {
    bg: "bg-[var(--color-surface-tertiary)]",
    text: "text-[var(--color-text-tertiary)]",
    dotClass: "bg-[var(--neutral-400)]",
  },
  building: {
    bg: "bg-[var(--color-warning-bg)]",
    text: "text-[var(--color-warning-text)]",
    dotClass: "bg-[var(--semantic-amber-400)] status-dot-pulse-fast",
  },
  crashed: {
    bg: "bg-[var(--color-error-bg)]",
    text: "text-[var(--color-error-text)]",
    dotClass: "bg-[var(--semantic-red-400)]",
  },
  idle: {
    bg: "bg-[var(--color-info-bg)]",
    text: "text-[var(--color-info-text)]",
    dotClass: "bg-[var(--semantic-blue-400)]",
  },
};

interface StatusBadgeProps {
  status: AgentStatus;
  className?: string;
}

export function StatusBadge({ status, className }: StatusBadgeProps) {
  const config = STATUS_CONFIG[status] ?? STATUS_CONFIG.stopped;

  return (
    <span
      className={cn(
        "status-badge",
        status,
        config.bg,
        config.text,
        className
      )}
    >
      <span className={cn("status-dot", config.dotClass)} />
      {status}
    </span>
  );
}
