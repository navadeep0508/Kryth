import * as React from "react";
import { cn } from "@/lib/utils";

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: "default" | "accent" | "success" | "warning" | "error" | "muted";
}

export function Badge({ className, variant = "default", ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium select-none",
        {
          "bg-surface2 text-muted border border-border":          variant === "default",
          "bg-accent/15 text-accent border border-accent/30":     variant === "accent",
          "bg-success/15 text-success border border-success/30":  variant === "success",
          "bg-warning/15 text-warning border border-warning/30":  variant === "warning",
          "bg-error/15 text-error border border-error/30":        variant === "error",
          "bg-transparent text-muted":                            variant === "muted",
        },
        className
      )}
      {...props}
    />
  );
}
