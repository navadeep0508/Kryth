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

  const [tab, setTab] = React.useState<"context" | "diff" | "approvals">("context");

  // Auto-switch to diff tab when a diff arrives
  React.useEffect(() => {
    if (pendingDiff) setTab("diff");
  }, [pendingDiff]);

  // Auto-switch to approvals tab when approval arrives
  React.useEffect(() => {
    if (pendingApprovals.length > 0) setTab("approvals");
  }, [pendingApprovals.length]);

  return (
    <aside
      className={cn(
        "flex flex-col border-l border-border bg-surface shrink-0",
        "transition-[width] duration-150 ease-out overflow-hidden",
        inspectorOpen ? "w-72" : "w-0"
      )}
    >
      <div className="flex flex-col h-full min-w-[288px]">
        {/* Tab bar */}
        <div className="flex border-b border-border shrink-0">
          <InspectorTab
            id="context"
            label="Context"
            icon={<FileText size={12} />}
            active={tab === "context"}
            onClick={() => setTab("context")}
          />
          <InspectorTab
            id="diff"
            label="Diff"
            icon={<GitCompare size={12} />}
            active={tab === "diff"}
            badge={pendingDiff ? 1 : 0}
            onClick={() => setTab("diff")}
          />
          <InspectorTab
            id="approvals"
            label="Approvals"
            icon={<CheckSquare size={12} />}
            active={tab === "approvals"}
            badge={pendingApprovals.length}
            onClick={() => setTab("approvals")}
          />
        </div>

        {/* Content */}
        <div className="flex-1 overflow-hidden">
          {tab === "context" && <ContextPane />}

          {tab === "diff" && (
            <Suspense fallback={<LoadingPane label="Loading diff viewer…" />}>
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
  id: string; label: string; icon: React.ReactNode;
  active: boolean; badge?: number; onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 px-3 py-2 text-xs transition-colors duration-120 relative",
        active
          ? "text-text border-b-2 border-accent -mb-px"
          : "text-muted hover:text-text"
      )}
    >
      {icon}
      {label}
      {!!badge && (
        <span className="ml-0.5 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-accent/20 text-accent">
          {badge}
        </span>
      )}
    </button>
  );
}

function ContextPane() {
  return (
    <div className="p-4 text-muted text-sm space-y-3">
      <p className="text-xs font-medium text-text/60 uppercase tracking-wide">
        Active Context
      </p>
      <p className="text-xs">
        KRYTH automatically includes relevant files and project context
        when answering your questions.
      </p>
    </div>
  );
}

function ApprovalsPane() {
  const pending = useApprovalStore((s) => s.pending);
  if (pending.length === 0) {
    return (
      <div className="p-4 text-center text-muted text-sm">
        No pending approvals
      </div>
    );
  }
  return (
    <div className="p-3 space-y-2 overflow-y-auto h-full">
      {pending.map((a) => (
        <div
          key={a.id}
          className="rounded-lg border border-border bg-surface2 p-3 text-sm"
        >
          <p className="text-text text-xs">{a.message}</p>
          <span
            className={cn("text-[10px] mt-1 inline-block", {
              "text-error":   a.risk === "high",
              "text-warning": a.risk === "medium",
              "text-muted":   a.risk === "low",
            })}
          >
            {a.risk} risk
          </span>
        </div>
      ))}
    </div>
  );
}

function LoadingPane({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center h-full text-muted text-sm">
      {label}
    </div>
  );
}
