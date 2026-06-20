import React, { memo, lazy, Suspense } from "react";
import { FileText, GitCompare, CheckSquare } from "lucide-react";
import { cn } from "@/lib/utils";
import { useUIStore } from "@/store/uiStore";
import { useEditorStore } from "@/store/editorStore";
import { useApprovalStore } from "@/store/approvalStore";

const DiffViewer = lazy(() => import("@/features/diff/DiffViewer"));

export const InspectorPanel = memo(function InspectorPanel() {
  const { inspectorOpen } = useUIStore();
  const pendingDiff      = useEditorStore((s) => s.pendingDiff);
  const pendingApprovals = useApprovalStore((s) => s.pending);
  const [tab, setTab]    = React.useState<"context" | "diff" | "approvals">("context");

  React.useEffect(() => { if (pendingDiff)               setTab("diff"); },      [pendingDiff]);
  React.useEffect(() => { if (pendingApprovals.length > 0) setTab("approvals"); }, [pendingApprovals.length]);

  return (
    <aside
      className={cn(
        "flex flex-col border-l border-[rgba(255,255,255,0.05)] bg-surface shrink-0",
        "transition-[width] duration-150 ease-out overflow-hidden",
        inspectorOpen ? "w-72" : "w-0"
      )}
    >
      <div className="flex flex-col h-full min-w-[288px]">
        {/* Tab strip */}
        <div className="flex border-b border-[rgba(255,255,255,0.05)] shrink-0">
          {(["context", "diff", "approvals"] as const).map((id) => (
            <InspectorTab
              key={id}
              id={id}
              label={id === "context" ? "Context" : id === "diff" ? "Diff" : "Approvals"}
              icon={id === "context" ? <FileText size={11} /> : id === "diff" ? <GitCompare size={11} /> : <CheckSquare size={11} />}
              active={tab === id}
              badge={id === "diff" ? (pendingDiff ? 1 : 0) : id === "approvals" ? pendingApprovals.length : 0}
              onClick={() => setTab(id)}
            />
          ))}
        </div>

        <div className="flex-1 overflow-hidden">
          {tab === "context"   && <ContextPane />}
          {tab === "diff"      && (
            <Suspense fallback={<PaneLoading />}>
              <DiffViewer />
            </Suspense>
          )}
          {tab === "approvals" && <ApprovalsPane />}
        </div>
      </div>
    </aside>
  );
});

function InspectorTab({
  label, icon, active, badge, onClick,
}: {
  id: string; label: string; icon: React.ReactNode; active: boolean; badge?: number; onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 px-3 py-2 text-xs transition-colors duration-100 relative",
        active ? "text-text border-b border-[rgba(255,255,255,0.15)] -mb-px" : "text-subtle hover:text-muted"
      )}
    >
      {icon}
      {label}
      {!!badge && (
        <span className="ml-0.5 w-4 h-4 flex items-center justify-center rounded-full text-[9px] font-medium bg-accent text-[#000] font-mono">
          {badge}
        </span>
      )}
    </button>
  );
}

function ContextPane() {
  return (
    <div className="p-4 space-y-3">
      <p className="text-[10px] font-medium text-subtle uppercase tracking-widest">Context</p>
      <div className="card p-3 space-y-2">
        <p className="text-xs text-muted leading-relaxed">
          KRYTH automatically includes relevant files and project context.
        </p>
      </div>
    </div>
  );
}

function ApprovalsPane() {
  const pending = useApprovalStore((s) => s.pending);
  if (pending.length === 0) {
    return (
      <div className="p-4 flex flex-col items-center justify-center h-full gap-2 text-subtle">
        <CheckSquare size={18} className="opacity-30" />
        <p className="text-xs">No pending approvals</p>
      </div>
    );
  }
  return (
    <div className="p-3 space-y-2 overflow-y-auto h-full">
      {pending.map((a) => (
        <div key={a.id} className="card p-3 space-y-1.5">
          <p className="text-xs text-text">{a.message}</p>
          <span
            className={cn(
              "inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-md",
              a.risk === "high"   && "bg-[rgba(239,68,68,0.1)] text-error/80",
              a.risk === "medium" && "bg-[rgba(245,158,11,0.1)] text-warning/80",
              a.risk === "low"    && "bg-[rgba(255,255,255,0.04)] text-subtle",
            )}
          >
            {a.risk} risk
          </span>
        </div>
      ))}
    </div>
  );
}

function PaneLoading() {
  return (
    <div className="flex items-center justify-center h-full text-subtle text-xs">Loading…</div>
  );
}
