import { memo, lazy, Suspense } from "react";
import { useEditorStore } from "@/store/editorStore";
import { bridge } from "@/lib/krythBridge";

const MonacoReact = lazy(() => import("@monaco-editor/react").then((m) => ({ default: m.DiffEditor })));

export default memo(function DiffEditorView() {
  const { pendingDiff, clearPendingDiff } = useEditorStore();

  if (!pendingDiff) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted text-sm">
        No diff pending
      </div>
    );
  }

  const handleApprove = async () => {
    await bridge.writeFile(pendingDiff.path, pendingDiff.after);
    await bridge.approve(pendingDiff.id, true).catch(() => {});
    clearPendingDiff();
  };

  const handleReject = async () => {
    await bridge.approve(pendingDiff.id, false).catch(() => {});
    clearPendingDiff();
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-3 py-2 border-b border-border shrink-0">
        <span className="text-xs text-muted truncate">
          {pendingDiff.path.replace(/\\/g, "/").split("/").pop()}
        </span>
        <div className="flex gap-2">
          <button
            onClick={handleReject}
            className="px-3 py-1 rounded-lg text-xs border border-border text-muted hover:text-text transition-colors duration-120"
          >
            Reject
          </button>
          <button
            onClick={handleApprove}
            className="px-3 py-1 rounded-lg text-xs bg-accent text-bg hover:bg-accent/90 transition-colors duration-120"
          >
            Apply
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-hidden">
        <Suspense fallback={<div className="flex items-center justify-center h-full text-muted text-sm">Loading diff…</div>}>
          <MonacoReact
            height="100%"
            original={pendingDiff.before}
            modified={pendingDiff.after}
            theme="kryth-dark"
            options={{
              readOnly: true,
              renderSideBySide: true,
              minimap: { enabled: false },
              fontSize: 12,
              scrollBeyondLastLine: false,
            }}
          />
        </Suspense>
      </div>
    </div>
  );
});
