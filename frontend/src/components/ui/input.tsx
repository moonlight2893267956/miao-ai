import * as React from "react";
import { cn } from "@/lib/utils";

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, ...props }, ref) => (
    <input
      ref={ref}
      type={type}
      className={cn(
        "flex h-9 w-full rounded-[var(--radius-md)] border border-[var(--color-border-default)] bg-[var(--color-surface-primary)] px-[13px] py-2 text-sm text-[var(--color-text-primary)] shadow-[var(--shadow-xs)] transition-all duration-150 ease-out file:mr-3 file:rounded-[var(--radius-sm)] file:border-0 file:bg-[var(--color-surface-tertiary)] file:px-3 file:py-1 file:text-xs file:font-medium file:text-[var(--color-text-secondary)] placeholder:text-[var(--color-text-tertiary)] hover:border-[var(--color-border-strong)] focus-visible:border-[var(--color-border-focus)] focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-primary/10 disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      {...props}
    />
  )
);
Input.displayName = "Input";
