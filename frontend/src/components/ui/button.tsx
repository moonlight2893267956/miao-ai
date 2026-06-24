"use client";

import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex select-none items-center justify-center gap-2 whitespace-nowrap rounded-[var(--radius-md)] border border-transparent text-sm font-medium transition-all duration-150 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50 [&_svg]:h-[18px] [&_svg]:w-[18px] [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default:
          "bg-[var(--color-brand)] text-white hover:bg-[var(--color-brand-hover)] hover:shadow-[var(--shadow-glow-violet)]",
        destructive:
          "bg-[var(--color-error-bg)] text-[var(--color-error-text)] hover:bg-[var(--color-error-icon)] hover:text-white",
        outline:
          "border-[var(--color-border-default)] bg-[var(--color-surface-primary)] text-[var(--color-text-primary)] hover:border-[var(--color-border-strong)] hover:bg-[var(--color-surface-hover)]",
        secondary:
          "border-[var(--color-border-default)] bg-[var(--color-surface-tertiary)] text-[var(--color-text-primary)] hover:bg-[var(--color-surface-hover)]",
        ghost:
          "bg-transparent text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text-primary)]",
        link: "text-[var(--color-brand)] underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-4 py-2",
        sm: "h-8 rounded-[var(--radius-sm)] px-3 text-xs",
        lg: "h-10 rounded-[var(--radius-md)] px-8",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button
      ref={ref}
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  )
);
Button.displayName = "Button";
