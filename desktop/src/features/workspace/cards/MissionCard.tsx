import { memo } from "react";
import { Check, X, Loader2, ChevronDown, ChevronRight, Rocket, Clock } from "lucide-react";
import { cn } from "@/lib/utils";
import { useWorkspaceStore, type MissionEvent, type MissionAgent } from "@/store/workspaceStore";

export const MissionCard = memo(function MissionCard({ event, turnId }: { event: MissionEvent; turnId: string }) {
  const toggleMissionCollapse = useWorkspaceStore((s) => s.toggleMissionCollapse);

  const progressPct = Math.round(event.progress * 100);
  const isDone = event.status === "done" || event.status === "failed";

  return (
    <div className="rounded-lg border border-border-soft bg-panel overflow-hidden animate-slide-up">
      {/* Header */}
      <button
        onClick={() => toggleMissionCollapse(turnId, event.id)}
        className="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-panel-hover transition-colors duration-100"
      >
        {event.collapsed ? <ChevronRight size={12} className="text-dim" /> : <ChevronDown size={12} className="text-dim" />}
        <Rocket size={13} className="text-accent" />
        <span className="text-xs font-semibold text-text truncate">{event.title}</span>
        <div className="flex-1" />
        <MissionStatusBadge status={event.status} />
        {/* Progress bar */}
        <div className="w-16 h-1.5 rounded-full bg-border-soft overflow-hidden ml-2">
          <div
            className={cn(
              "h-full rounded-full transition-all duration-500 ease-out",
              event.status === "failed" ? "bg-danger" : "bg-accent",
            )}
            style={{ width: `${progressPct}%` }}
          />
        </div>
        <span className="text-2xs text-dim font-mono ml-1.5">{progressPct}%</span>
      </button>

      {/* Body */}
      {!event.collapsed && (
        <div className="px-3 pb-3 space-y-1">
          {event.agents.map((agent) => (
            <AgentRow key={agent.id} agent={agent} />
          ))}

          {/* Completion banner */}
          {isDone && (
            <div
              className={cn(
                "mt-2 px-3 py-2 rounded-md text-xs flex items-center gap-2",
                event.status === "done" && "bg-success/10 text-success",
                event.status === "failed" && "bg-danger/10 text-danger",
              )}
            >
              {event.status === "done" ? <Check size={12} /> : <X size={12} />}
              <span className="font-medium">
                {event.status === "done" ? "Mission complete" : "Mission failed"}
              </span>
              {event.duration != null && (
                <span className="ml-auto flex items-center gap-1 text-2xs opacity-80">
                  <Clock size={10} />
                  {event.duration.toFixed(1)}s
                </span>
              )}
            </div>
          )}
          {isDone && event.summary && (
            <p className="text-xs text-muted mt-1 pl-1">{event.summary}</p>
          )}
        </div>
      )}
    </div>
  );
});

function AgentRow({ agent }: { agent: MissionAgent }) {
  return (
    <div
      className={cn(
        "flex items-center gap-2.5 py-1.5 px-2 rounded-sm transition-colors duration-150",
        agent.status === "running" && "bg-accent-muted",
      )}
    >
      <AgentStatusIcon status={agent.status} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className={cn(
            "text-xs font-medium truncate",
            agent.status === "done" && "text-muted",
            agent.status === "running" && "text-text",
            agent.status === "queued" && "text-dim",
            agent.status === "failed" && "text-danger",
          )}>
            {agent.name}
          </span>
        </div>
        <p className={cn(
          "text-2xs truncate",
          agent.status === "running" ? "text-muted" : "text-dim",
        )}>
          {agent.task}
        </p>
      </div>
      {/* Mini progress bar for running agents */}
      {agent.status === "running" && agent.progress != null && (
        <div className="w-10 h-1 rounded-full bg-border-soft overflow-hidden">
          <div
            className="h-full bg-accent rounded-full transition-all duration-300"
            style={{ width: `${Math.round((agent.progress ?? 0) * 100)}%` }}
          />
        </div>
      )}
    </div>
  );
}

function AgentStatusIcon({ status }: { status: MissionAgent["status"] }) {
  switch (status) {
    case "done":
      return (
        <div className="w-4 h-4 rounded-full bg-success/15 flex items-center justify-center shrink-0">
          <Check size={10} className="text-success" strokeWidth={3} />
        </div>
      );
    case "running":
      return (
        <div className="w-4 h-4 rounded-full bg-accent/15 flex items-center justify-center shrink-0">
          <Loader2 size={10} className="text-accent animate-spin" />
        </div>
      );
    case "failed":
      return (
        <div className="w-4 h-4 rounded-full bg-danger/15 flex items-center justify-center shrink-0">
          <X size={10} className="text-danger" strokeWidth={3} />
        </div>
      );
    default:
      return (
        <div className="w-4 h-4 rounded-full bg-panel-hover flex items-center justify-center shrink-0">
          <div className="w-2 h-2 rounded-full bg-faint" />
        </div>
      );
  }
}

function MissionStatusBadge({ status }: { status: MissionEvent["status"] }) {
  return (
    <span
      className={cn(
        "text-2xs font-medium px-1.5 py-0.5 rounded",
        status === "running" && "bg-accent-muted text-accent",
        status === "done" && "bg-success-muted text-success",
        status === "failed" && "bg-danger-muted text-danger",
      )}
    >
      {status === "running" ? "Running" : status === "done" ? "Done" : "Failed"}
    </span>
  );
}
