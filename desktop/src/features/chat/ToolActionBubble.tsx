import { memo } from "react";
import { CheckCircle, XCircle, Loader } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ToolAction } from "@/store/chatStore";

export const ToolActionBubble = memo(function ToolActionBubble({ action }: { action: ToolAction }) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs",
        "border transition-all duration-120",
        action.status === "running"  && "border-border bg-surface2 text-muted",
        action.status === "done"     && "border-success/20 bg-success/5 text-success/70",
        action.status === "failed"   && "border-error/20 bg-error/5 text-error/70",
      )}
    >
      {action.status === "running" && <Loader size={11} className="animate-spin shrink-0" />}
      {action.status === "done"    && <CheckCircle size={11} className="shrink-0" />}
      {action.status === "failed"  && <XCircle size={11} className="shrink-0" />}
      <span className="truncate">{action.label}</span>
    </div>
  );
});
