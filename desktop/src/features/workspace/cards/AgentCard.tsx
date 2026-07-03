import { memo } from "react";
import { Bot, Loader2, Check, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { AgentEvent } from "@/store/workspaceStore";

export const AgentCard = memo(function AgentCard({ event }: { event: AgentEvent }) {
  return (
    <div className="rounded-md border border-border-soft bg-panel px-3 py-2.5 animate-fade-in">
      <div className="flex items-center gap-2.5">
        <div className="w-7 h-7 rounded-md bg-accent-muted border border-border flex items-center justify-center shrink-0">
          <Bot size={13} className="text-accent" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-text">{event.name}</span>
            {event.role && (
              <span className="text-2xs text-dim px-1.5 py-0.5 rounded bg-panel-hover border border-border-soft">
                {event.role}
              </span>
            )}
          </div>
          <p className="text-xs text-muted truncate mt-0.5">{event.task}</p>
        </div>
        <AgentStatusBadge status={event.status} />
      </div>
    </div>
  );
});

function AgentStatusBadge({ status }: { status: string }) {
  return (
    <div
      className={cn(
        "flex items-center gap-1 px-2 py-0.5 rounded text-2xs font-medium",
        status === "running" && "bg-accent-muted text-accent",
        status === "success" && "bg-success-muted text-success",
        status === "failed" && "bg-danger-muted text-danger",
        status === "queued" && "bg-panel-hover text-dim",
      )}
    >
      {status === "running" && <Loader2 size={9} className="animate-spin" />}
      {status === "success" && <Check size={9} />}
      {status === "failed" && <X size={9} />}
      <span className="capitalize">{status}</span>
    </div>
  );
}
