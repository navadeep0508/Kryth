import { memo, useCallback } from "react";
import { ShieldAlert, Check, X, AlertTriangle, Clock, ShieldCheck } from "lucide-react";
import { cn } from "@/lib/utils";
import { useWorkspaceStore, type ApprovalEvent } from "@/store/workspaceStore";
import { bridge } from "@/lib/krythBridge";

export const ApprovalCard = memo(function ApprovalCard({ event, turnId }: { event: ApprovalEvent; turnId: string }) {
  const resolveApproval = useWorkspaceStore((s) => s.resolveApproval);

  const handleApprove = useCallback(() => {
    bridge.approve(event.id, true, false).catch(() => {});
    resolveApproval(turnId, event.id, true);
  }, [event.id, turnId, resolveApproval]);

  const handleAlwaysAllow = useCallback(() => {
    bridge.approve(event.id, true, true).catch(() => {});
    resolveApproval(turnId, event.id, true);
  }, [event.id, turnId, resolveApproval]);

  const handleReject = useCallback(() => {
    bridge.approve(event.id, false, false).catch(() => {});
    resolveApproval(turnId, event.id, false);
  }, [event.id, turnId, resolveApproval]);

  const isHigh = event.risk === "high";
  const isResolved = event.resolved;

  if (isResolved) {
    return (
      <div className="flex items-center gap-2 px-3 py-1.5 rounded-md border border-border-soft bg-panel/50 text-xs text-dim animate-fade-in">
        <Check size={11} className="text-success" />
        <span className="truncate">{event.message}</span>
        <span className="ml-auto text-[10px] text-success font-medium">Approved</span>
      </div>
    );
  }

  return (
    <div className={cn(
      "rounded-lg border overflow-hidden animate-slide-up",
      isHigh ? "border-danger/30 bg-danger/[0.03]" : "border-warning/25 bg-warning/[0.03]",
    )}>
      {/* Header */}
      <div className="px-3 py-2.5 flex items-start gap-2.5">
        <div className={cn(
          "w-7 h-7 rounded-lg flex items-center justify-center shrink-0",
          isHigh ? "bg-danger/10" : "bg-warning/10",
        )}>
          {isHigh ? (
            <AlertTriangle size={13} className="text-danger" />
          ) : (
            <ShieldAlert size={13} className="text-warning" />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className={cn(
              "text-[10px] font-semibold uppercase tracking-wider",
              isHigh ? "text-danger" : "text-warning",
            )}>
              {isHigh ? "High Risk Action" : "Action Approval"}
            </span>
            <RiskBadge risk={event.risk} />
          </div>
          <p className="text-xs text-text mt-1 leading-relaxed">{event.message}</p>
          {event.detail && (
            <p className="text-[11px] text-dim mt-1">{event.detail}</p>
          )}
        </div>
      </div>

      {/* Actions — 3 options */}
      <div className="flex items-center gap-2 px-3 py-2 border-t border-border-soft/50">
        <div className="flex items-center gap-1.5 mr-auto">
          <Clock size={10} className="text-dim animate-pulse" />
          <span className="text-[10px] text-dim">Waiting…</span>
        </div>
        <button
          onClick={handleReject}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[11px] font-medium border border-border text-muted hover:text-danger hover:border-danger/30 transition-colors duration-100"
        >
          <X size={10} />
          Reject
        </button>
        <button
          onClick={handleApprove}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[11px] font-medium border border-success/30 text-success hover:bg-success/10 transition-colors duration-100"
        >
          <Check size={10} />
          Approve
        </button>
        <button
          onClick={handleAlwaysAllow}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[11px] font-medium bg-success text-white hover:bg-success/90 transition-colors duration-100"
        >
          <ShieldCheck size={10} />
          Allow Session
        </button>
      </div>
    </div>
  );
});

function RiskBadge({ risk }: { risk: string }) {
  return (
    <span className={cn(
      "text-[9px] px-1.5 py-0.5 rounded-full font-semibold capitalize tracking-wide",
      risk === "low" && "bg-panel-hover text-dim",
      risk === "medium" && "bg-warning/10 text-warning",
      risk === "high" && "bg-danger/10 text-danger",
    )}>
      {risk}
    </span>
  );
}
