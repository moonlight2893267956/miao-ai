import * as React from "react";
import { cn } from "@/lib/utils";

export type TextareaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement>;

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        "flex min-h-[96px] w-full rounded-[var(--radius-md)] border border-[var(--color-border-default)] bg-[var(--color-surface-primary)] px-[13px] py-2 text-sm text-[var(--color-text-primary)] shadow-[var(--shadow-xs)] transition-all duration-150 ease-out placeholder:text-[var(--color-text-tertiary)] hover:border-[var(--color-border-strong)] focus-visible:border-[var(--color-border-focus)] focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-primary/10 disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      {...props}
    />
  )
);
Textarea.displayName = "Textarea";
