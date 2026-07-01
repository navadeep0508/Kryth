import React, { memo, useEffect, useRef, useCallback } from "react";
import { FolderOpen, RefreshCw, FilePlus, FolderPlus, Search } from "lucide-react";
import { useProjectStore } from "@/store/projectStore";
import { useWorkspaceStore } from "@/store/workspaceStore";
import { useEditorStore } from "@/store/editorStore";
import { bridge } from "@/lib/krythBridge";
import { FileTree } from "./FileTree";

async function handleOpenFolder() {
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    const path = await invoke<string | null>("open_folder_dialog");
    if (path) {
      await useProjectStore.getState().openFolder(path);
      useWorkspaceStore.getState().setCwd(path);
    }
  } catch (e) {
    console.warn("[KRYTH] Tauri invoke unavailable:", e);
  }
}

export default memo(function FileExplorer() {
  const { cwd, restoreLastFolder } = useProjectStore();
  const containerRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = React.useState(400);
  const [loading, setLoading] = React.useState(false);

  const refresh = useCallback(async () => {
    if (!cwd) return;
    setLoading(true);
    try {
      await useProjectStore.getState().openFolder(cwd);
    } finally {
      setLoading(false);
    }
  }, [cwd]);

  useEffect(() => {
    if (!cwd) restoreLastFolder();
  }, []); // eslint-disable-line

  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver(([entry]) => setHeight(entry.contentRect.height));
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  const handleNewFile = useCallback(async () => {
    if (!cwd) return;
    const name = prompt("New file name:");
    if (!name) return;
    const sep = cwd.includes("/") ? "/" : "\\";
    const newPath = `${cwd}${sep}${name}`;
    try {
      await bridge.writeFile(newPath, "");
      useEditorStore.getState().openTab({ path: newPath, filename: name, content: "", language: "" });
      await refresh();
    } catch (e) {
      alert(`Failed: ${e}`);
    }
  }, [cwd, refresh]);

  const handleNewFolder = useCallback(async () => {
    if (!cwd) return;
    const name = prompt("New folder name:");
    if (!name) return;
    const sep = cwd.includes("/") ? "/" : "\\";
    const gitkeep = `${cwd}${sep}${name}${sep}.gitkeep`;
    try {
      await bridge.writeFile(gitkeep, "");
      await refresh();
    } catch (e) {
      alert(`Failed: ${e}`);
    }
  }, [cwd, refresh]);

  if (!cwd) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 p-4 text-center">
        <FolderOpen size={24} className="text-dim/40" />
        <p className="text-xs text-dim">No folder open</p>
        <button
          onClick={handleOpenFolder}
          className="px-3 py-1.5 text-xs rounded-md border border-border text-muted hover:text-text hover:bg-panel-hover transition-colors"
        >
          Open Folder
        </button>
      </div>
    );
  }

  const folderName = cwd.replace(/\\/g, "/").split("/").pop() ?? cwd;

  return (
    <div className="flex-1 h-full flex flex-col overflow-hidden">
      {/* Header with folder name + actions */}
      <div className="flex items-center gap-1 px-2 py-1.5 border-b border-border-soft shrink-0">
        <span className="text-[11px] font-medium text-text truncate flex-1" title={cwd}>
          {folderName}
        </span>
        <button onClick={handleNewFile} className="p-1 rounded text-dim hover:text-muted transition-colors" title="New File">
          <FilePlus size={12} />
        </button>
        <button onClick={handleNewFolder} className="p-1 rounded text-dim hover:text-muted transition-colors" title="New Folder">
          <FolderPlus size={12} />
        </button>
        <button onClick={refresh} disabled={loading} className="p-1 rounded text-dim hover:text-muted transition-colors disabled:opacity-50" title="Refresh">
          <RefreshCw size={11} className={loading ? "animate-spin" : ""} />
        </button>
      </div>

      {/* Tree */}
      <div ref={containerRef} className="flex-1 overflow-hidden">
        <FileTree height={height} />
      </div>
    </div>
  );
});
