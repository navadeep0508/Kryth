import { memo } from "react";
import { CheckCircle2, XCircle, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ToolAction } from "@/store/chatStore";

export const ToolActionBubble = memo(function ToolActionBubble({ action }: { action: ToolAction }) {
  return (
    <div
      className={cn(
        "inline-flex items-center gap-2 px-2.5 py-1 rounded-md text-xs font-mono",
        "border transition-colors duration-100",
        action.status === "running" && "border-[rgba(255,255,255,0.06)] bg-surface2 text-muted",
        action.status === "done"    && "border-[rgba(34,197,94,0.15)] bg-[rgba(34,197,94,0.05)] text-success/70",
        action.status === "failed"  && "border-[rgba(239,68,68,0.15)] bg-[rgba(239,68,68,0.05)] text-error/70",
      )}
    >
      {action.status === "running" && <Loader2 size={10} className="animate-spin shrink-0 text-subtle" />}
      {action.status === "done"    && <CheckCircle2 size={10} className="shrink-0" />}
      {action.status === "failed"  && <XCircle size={10} className="shrink-0" />}
      <span className="truncate max-w-[280px]">{action.label}</span>
    </div>
  );
});
