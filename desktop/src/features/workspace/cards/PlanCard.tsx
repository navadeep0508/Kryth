import { memo } from "react";
import { Check, Circle, ArrowRight, X, ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { useWorkspaceStore, type PlanEvent, type PlanStep } from "@/store/workspaceStore";

export const PlanCard = memo(function PlanCard({ event, turnId }: { event: PlanEvent; turnId: string }) {
  const togglePlanCollapse = useWorkspaceStore((s) => s.togglePlanCollapse);

  const doneCount = event.steps.filter((s) => s.status === "done").length;
  const totalCount = event.steps.length;
  const progress = totalCount > 0 ? (doneCount / totalCount) * 100 : 0;

  return (
    <div className="rounded-lg border border-border-soft bg-panel overflow-hidden animate-slide-up">
      {/* Header */}
      <button
        onClick={() => togglePlanCollapse(turnId, event.id)}
        className="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-panel-hover transition-colors duration-100"
      >
        {event.collapsed ? <ChevronRight size={12} className="text-dim" /> : <ChevronDown size={12} className="text-dim" />}
        <span className="text-xs font-semibold text-muted uppercase tracking-wide">Plan</span>
        <div className="flex-1" />
        <span className="text-2xs text-dim font-mono">{doneCount}/{totalCount}</span>
        {/* Mini progress bar */}
        <div className="w-12 h-1 rounded-full bg-border-soft overflow-hidden ml-1.5">
          <div
            className="h-full bg-success rounded-full transition-all duration-300 ease-out"
            style={{ width: `${progress}%` }}
          />
        </div>
      </button>

      {/* Steps */}
      {!event.collapsed && (
        <div className="px-3 pb-3 space-y-0.5">
          {event.steps.map((step, i) => (
            <StepRow key={step.id} step={step} index={i} />
          ))}
        </div>
      )}
    </div>
  );
});

function StepRow({ step, index }: { step: PlanStep; index: number }) {
  return (
    <div className={cn(
      "flex items-center gap-2.5 py-1.5 pl-1 rounded-sm transition-colors duration-100",
      step.status === "active" && "bg-accent-muted",
    )}>
      <StepIcon status={step.status} />
      <span
        className={cn(
          "text-xs transition-all duration-150",
          step.status === "done" && "text-muted line-through opacity-70",
          step.status === "active" && "text-text font-medium",
          step.status === "pending" && "text-dim",
          step.status === "failed" && "text-danger",
        )}
      >
        {step.label}
      </span>
    </div>
  );
}

function StepIcon({ status }: { status: PlanStep["status"] }) {
  switch (status) {
    case "done":
      return (
        <div className="w-4 h-4 rounded-full bg-success/15 flex items-center justify-center shrink-0 animate-scale-check">
          <Check size={10} className="text-success" strokeWidth={3} />
        </div>
      );
    case "active":
      return (
        <div className="w-4 h-4 rounded-full bg-accent/15 flex items-center justify-center shrink-0">
          <ArrowRight size={10} className="text-accent" />
        </div>
      );
    case "failed":
      return (
        <div className="w-4 h-4 rounded-full bg-danger/15 flex items-center justify-center shrink-0">
          <X size={10} className="text-danger" />
        </div>
      );
    default:
      return (
        <div className="w-4 h-4 flex items-center justify-center shrink-0">
          <Circle size={8} className="text-faint" />
        </div>
      );
  }
}
