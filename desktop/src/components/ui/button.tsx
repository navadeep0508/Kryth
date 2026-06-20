import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-medium transition-all duration-120 focus-visible:outline-none disabled:pointer-events-none disabled:opacity-40 select-none",
  {
    variants: {
      variant: {
        default:   "bg-accent text-bg hover:bg-accent/90 shadow-sm",
        secondary: "bg-surface2 text-text hover:bg-surface2/80 border border-border",
        ghost:     "text-muted hover:bg-surface2 hover:text-text",
        danger:    "bg-error/10 text-error hover:bg-error/20 border border-error/20",
        success:   "bg-success/10 text-success hover:bg-success/20 border border-success/20",
        outline:   "border border-border bg-transparent text-text hover:bg-surface2",
      },
      size: {
        sm:   "h-7  px-2.5 text-xs rounded-md",
        md:   "h-8  px-3   text-sm",
        lg:   "h-10 px-4   text-sm",
        icon: "h-8  w-8    p-0",
      },
    },
    defaultVariants: { variant: "default", size: "md" },
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
