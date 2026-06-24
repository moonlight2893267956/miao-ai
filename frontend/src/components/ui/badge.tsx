import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold tracking-[0.02em] transition-colors",
  {
    variants: {
      variant: {
        default:
          "border-transparent bg-[var(--color-brand)] text-white hover:bg-[var(--color-brand-hover)]",
        secondary:
          "border-transparent bg-[var(--color-surface-tertiary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)]",
        destructive:
          "border-transparent bg-[var(--color-error-bg)] text-[var(--color-error-text)] hover:bg-[var(--color-error-icon)] hover:text-white",
        outline: "border-[var(--color-border-default)] text-[var(--color-text-primary)]",
      },
    },
    defaultVariants: { variant: "default" },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}
