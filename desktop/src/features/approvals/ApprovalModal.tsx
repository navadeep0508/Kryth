import { memo, useEffect, useCallback } from "react";
import { ShieldAlert, CheckCheck } from "lucide-react";
import { useApprovalStore } from "@/store/approvalStore";
import { bridge } from "@/lib/krythBridge";
import { ToolRequest } from "./ToolRequest";

export default memo(function ApprovalModal() {
  const { pending, resolve } = useApprovalStore();

  const approveAll = useCallback(async () => {
    for (const req of pending) {
      await bridge.approve(req.id, true).catch(() => {});
      resolve(req.id, true);
    }
  }, [pending, resolve]);

  const rejectAll = useCallback(async () => {
    for (const req of pending) {
      await bridge.approve(req.id, false).catch(() => {});
      resolve(req.id, false);
    }
  }, [pending, resolve]);

  const approveOne = useCallback(
    async (id: string) => {
      await bridge.approve(id, true).catch(() => {});
      resolve(id, true);
    },
    [resolve]
  );

  const rejectOne = useCallback(
    async (id: string) => {
      await bridge.approve(id, false).catch(() => {});
      resolve(id, false);
    },
    [resolve]
  );

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (pending.length === 0) return;
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        approveAll();
      }
      if (e.key === "Escape") {
        e.preventDefault();
        rejectAll();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [pending.length, approveAll, rejectAll]);

  if (pending.length === 0) return null;

  const hasHighRisk = pending.some((r) => r.risk === "high");

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-bg/80 backdrop-blur-sm"
        onClick={rejectAll}
      />

      {/* Modal */}
      <div className="relative w-[480px] max-h-[70vh] rounded-2xl border border-border bg-surface shadow-panel flex flex-col animate-slide-up">
        {/* Header */}
        <div className="flex items-center gap-3 p-4 border-b border-border shrink-0">
          <div className="w-9 h-9 rounded-xl bg-warning/10 border border-warning/20 flex items-center justify-center">
            <ShieldAlert size={16} className="text-warning" />
          </div>
          <div className="flex-1">
            <h2 className="text-sm font-semibold text-text">
              {pending.length === 1 ? "Action requires approval" : `${pending.length} actions require approval`}
            </h2>
            <p className="text-xs text-muted mt-0.5">
              KRYTH wants to perform the following
            </p>
          </div>
        </div>

        {/* Request list */}
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {pending.map((req) => (
            <ToolRequest
              key={req.id}
              request={req}
              onApprove={() => approveOne(req.id)}
              onReject={() => rejectOne(req.id)}
            />
          ))}
        </div>

        {/* Footer — bulk actions */}
        {pending.length > 1 && (
          <div className="flex items-center justify-between p-4 border-t border-border shrink-0">
            <button
              onClick={rejectAll}
              className="px-4 py-2 rounded-lg text-sm border border-border text-muted hover:text-text transition-colors duration-120"
            >
              Deny all (Esc)
            </button>
            <button
              onClick={approveAll}
              className={
                hasHighRisk
                  ? "flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm bg-error/10 text-error border border-error/30 hover:bg-error/20 transition-colors duration-120"
                  : "flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm bg-accent text-bg font-medium hover:bg-accent/90 transition-colors duration-120"
              }
            >
              <CheckCheck size={14} />
              Allow all (Enter)
            </button>
          </div>
        )}

        {/* Single-item hint */}
        {pending.length === 1 && (
          <p className="text-center text-xs text-muted pb-3">
            Enter to allow · Esc to deny
          </p>
        )}
      </div>
    </div>
  );
});
