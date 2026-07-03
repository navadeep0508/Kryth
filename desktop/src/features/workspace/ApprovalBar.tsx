import { memo, useCallback, useMemo } from "react";
import { ShieldAlert, Check, X, AlertTriangle, ShieldCheck } from "lucide-react";
import { cn } from "@/lib/utils";
import { useWorkspaceStore, type ApprovalEvent } from "@/store/workspaceStore";
import { bridge } from "@/lib/krythBridge";

interface PendingApproval {
  turnId: string;
  event: ApprovalEvent;
}

export const ApprovalBar = memo(function ApprovalBar() {
  const turns = useWorkspaceStore((s) => s.turns);
  const resolveApproval = useWorkspaceStore((s) => s.resolveApproval);

  // Collect all unresolved approvals across turns
  const pending: PendingApproval[] = useMemo(() => {
    const items: PendingApproval[] = [];
    for (const turn of turns) {
      for (const event of turn.events) {
        if (event.type === "approval_required" && !event.resolved) {
          items.push({ turnId: turn.id, event });
        }
      }
    }
    return items;
  }, [turns]);

  const handleApprove = useCallback((item: PendingApproval) => {
    bridge.approve(item.event.id, true, false).catch(() => {});
    resolveApproval(item.turnId, item.event.id, true);
  }, [resolveApproval]);

  const handleAllowSession = useCallback((item: PendingApproval) => {
    bridge.approve(item.event.id, true, true).catch(() => {});
    resolveApproval(item.turnId, item.event.id, true);
  }, [resolveApproval]);

  const handleReject = useCallback((item: PendingApproval) => {
    bridge.approve(item.event.id, false, false).catch(() => {});
    resolveApproval(item.turnId, item.event.id, false);
  }, [resolveApproval]);

  const handleApproveAll = useCallback(() => {
    for (const item of pending) {
      bridge.approve(item.event.id, true, true).catch(() => {});
      resolveApproval(item.turnId, item.event.id, true);
    }
  }, [pending, resolveApproval]);

  if (pending.length === 0) return null;

  // Show the most recent approval in the bar
  const current = pending[pending.length - 1];
  const isHigh = current.event.risk === "high";

  return (
    <div className={cn(
      "border-t px-4 py-2.5 shrink-0 animate-slide-up",
      isHigh ? "border-danger/30 bg-danger/[0.04]" : "border-warning/30 bg-warning/[0.04]",
    )}>
      <div className="flex items-center gap-3">
        {/* Icon */}
        <div className={cn(
          "w-8 h-8 rounded-lg flex items-center justify-center shrink-0",
          isHigh ? "bg-danger/10" : "bg-warning/10",
        )}>
          {isHigh ? (
            <AlertTriangle size={15} className="text-danger" />
          ) : (
            <ShieldAlert size={15} className="text-warning" />
          )}
        </div>

        {/* Message */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className={cn(
              "text-[10px] font-semibold uppercase tracking-wider",
              isHigh ? "text-danger" : "text-warning",
            )}>
              {isHigh ? "High Risk" : "Approval Required"}
            </span>
            {pending.length > 1 && (
              <span className="text-[10px] text-dim">+{pending.length - 1} more</span>
            )}
          </div>
          <p className="text-xs text-text mt-0.5 truncate">{current.event.message}</p>
        </div>

        {/* Actions — 3 buttons */}
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => handleReject(current)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[11px] font-medium border border-border text-muted hover:text-danger hover:border-danger/30 transition-colors duration-100"
          >
            <X size={11} />
            Reject
          </button>
          <button
            onClick={() => handleApprove(current)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[11px] font-medium border border-success/30 text-success hover:bg-success/10 transition-colors duration-100"
          >
            <Check size={11} />
            Approve
          </button>
          <button
            onClick={() => handleAllowSession(current)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[11px] font-medium bg-success text-white hover:bg-success/90 transition-colors duration-100"
          >
            <ShieldCheck size={11} />
            Allow Session
          </button>
        </div>
      </div>
    </div>
  );
});
