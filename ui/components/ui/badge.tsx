import { cn } from "@/lib/utils";
import * as React from "react";

type BadgeVariant = "default" | "secondary" | "outline" | "destructive";

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
}

const VARIANT_CLASSES: Record<BadgeVariant, string> = {
  default: "bg-foreground/10 text-foreground",
  secondary: "bg-muted text-muted-foreground",
  outline: "bg-transparent",
  destructive: "border-destructive/50 bg-destructive/10 text-destructive",
};

export function Badge({
  className,
  children,
  variant = "default",
  ...props
}: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium",
        VARIANT_CLASSES[variant],
        className,
      )}
      {...props}
    >
      {children}
    </span>
  );
}
