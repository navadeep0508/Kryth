import { memo } from "react";
import { Lightbulb } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ReflectionEvent } from "@/store/workspaceStore";

const CATEGORY_CONFIG = {
  failure_analysis: { label: "Analysis", borderColor: "border-l-danger", iconColor: "text-danger/70" },
  success_pattern: { label: "Pattern", borderColor: "border-l-success", iconColor: "text-success/70" },
  improvement: { label: "Insight", borderColor: "border-l-accent", iconColor: "text-accent/70" },
} as const;

export const ReflectionCard = memo(function ReflectionCard({ event }: { event: ReflectionEvent }) {
  const config = CATEGORY_CONFIG[event.category];

  return (
    <div
      className={cn(
        "rounded-md border border-border-soft bg-panel px-3 py-2 animate-fade-in",
        "border-l-2",
        config.borderColor,
      )}
    >
      <div className="flex items-start gap-2">
        <Lightbulb size={12} className={cn("mt-0.5 shrink-0", config.iconColor)} />
        <div className="flex-1 min-w-0">
          <span className="text-2xs font-semibold text-dim uppercase tracking-wide">
            {config.label}
          </span>
          <p className="text-xs text-muted italic mt-0.5 leading-relaxed">
            {event.insight}
          </p>
        </div>
      </div>
    </div>
  );
});
