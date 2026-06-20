import { memo, lazy, Suspense } from "react";
import { useEditorStore } from "@/store/editorStore";
import { bridge } from "@/lib/krythBridge";

const MonacoDiffEditor = lazy(() =>
  import("@monaco-editor/react").then((m) => ({ default: m.DiffEditor }))
);

export default memo(function DiffViewer() {
  const { pendingDiff, clearPendingDiff } = useEditorStore();

  if (!pendingDiff) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 text-muted">
        <p className="text-sm">No pending changes</p>
        <p className="text-xs opacity-60">KRYTH will show file changes here before applying</p>
      </div>
    );
  }

  const filename = pendingDiff.path.replace(/\\/g, "/").split("/").pop() ?? pendingDiff.path;

  const handleApply = async () => {
    try {
      await bridge.writeFile(pendingDiff.path, pendingDiff.after);
      await bridge.approve(pendingDiff.id, true).catch(() => {});
    } finally {
      clearPendingDiff();
    }
  };

  const handleReject = async () => {
    await bridge.approve(pendingDiff.id, false).catch(() => {});
    clearPendingDiff();
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-3 py-2 border-b border-border shrink-0">
        <span className="text-xs font-medium text-text truncate max-w-[140px]">{filename}</span>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={handleReject}
            className="px-2.5 py-1 rounded-lg text-xs border border-border text-muted hover:text-text transition-colors duration-120"
          >
            Reject
          </button>
          <button
            onClick={handleApply}
            className="px-2.5 py-1 rounded-lg text-xs bg-accent text-bg font-medium hover:bg-accent/90 transition-colors duration-120"
          >
            Apply
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-hidden">
        <Suspense
          fallback={
            <div className="flex items-center justify-center h-full text-muted text-sm">
              Loading diff…
            </div>
          }
        >
          <MonacoDiffEditor
            height="100%"
            original={pendingDiff.before}
            modified={pendingDiff.after}
            theme="kryth-dark"
            options={{
              readOnly: true,
              renderSideBySide: false,
              minimap: { enabled: false },
              fontSize: 12,
              scrollBeyondLastLine: false,
              padding: { top: 8 },
            }}
          />
        </Suspense>
      </div>
    </div>
  );
});
