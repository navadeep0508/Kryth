import React, { memo, useEffect, useRef } from "react";
import { FolderOpen, RefreshCw, Search } from "lucide-react";
import { useProjectStore } from "@/store/projectStore";
import { useChatStore } from "@/store/chatStore";
import { bridge } from "@/lib/krythBridge";
import { FileTree } from "./FileTree";
import { FileSearch } from "./FileSearch";

export default memo(function FileExplorer() {
  const { cwd, setRoots, setCwd } = useProjectStore();
  const chatCwd = useChatStore((s) => s.cwd);
  const containerRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = React.useState(400);
  const [loading, setLoading] = React.useState(false);

  const effectiveCwd = cwd || chatCwd;

  const load = React.useCallback(async (dir: string) => {
    if (!dir) return;
    setLoading(true);
    try {
      const entries = await bridge.listFiles(dir);
      setRoots(entries);
      setCwd(dir);
    } catch {
      // silently handle - no project open
    } finally {
      setLoading(false);
    }
  }, [setRoots, setCwd]);

  // Load when cwd changes
  useEffect(() => {
    if (effectiveCwd) load(effectiveCwd);
  }, [effectiveCwd, load]);

  // Track container height for react-window
  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver(([entry]) => setHeight(entry.contentRect.height));
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  return (
    <div className="w-56 border-r border-border bg-surface flex flex-col shrink-0 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-1.5 px-3 py-2 border-b border-border shrink-0">
        <span className="text-xs font-medium text-muted uppercase tracking-wide flex-1">
          {effectiveCwd
            ? effectiveCwd.replace(/\\/g, "/").split("/").pop()
            : "Explorer"}
        </span>
        <button
          onClick={() => effectiveCwd && load(effectiveCwd)}
          disabled={loading}
          className="p-1 rounded text-muted hover:text-text transition-colors duration-120 disabled:opacity-50"
          title="Refresh"
        >
          <RefreshCw size={11} className={loading ? "animate-spin" : ""} />
        </button>
        <button
          onClick={() => {/* Ctrl+P is handled globally in FileSearch */}}
          className="p-1 rounded text-muted hover:text-text transition-colors duration-120"
          title="Search files (Ctrl+P)"
        >
          <Search size={11} />
        </button>
      </div>

      {/* Tree */}
      <div ref={containerRef} className="flex-1 overflow-hidden">
        {!effectiveCwd ? (
          <EmptyProject />
        ) : (
          <FileTree height={height} />
        )}
      </div>

      <FileSearch />
    </div>
  );
});

function EmptyProject() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-2 text-muted p-4 text-center">
      <FolderOpen size={22} className="text-muted/40" />
      <p className="text-xs">No folder open</p>
      <p className="text-xs opacity-60">Start a chat to open a project</p>
    </div>
  );
}
