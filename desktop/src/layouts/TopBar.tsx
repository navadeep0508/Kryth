import React, { memo } from "react";
import { PanelLeft, PanelRight, Settings, ChevronDown, Search, Loader2 } from "lucide-react";
import { invoke } from "@tauri-apps/api/core";
import { cn } from "@/lib/utils";
import { useUIStore } from "@/store/uiStore";
import { useChatStore } from "@/store/chatStore";
import { useProjectStore } from "@/store/projectStore";
import { bridge } from "@/lib/krythBridge";

export const TopBar = memo(function TopBar() {
  const { toggleSidebar, toggleInspector, openPalette, connStatus } = useUIStore();
  const status     = useChatStore((s) => s.status);
  const cwd        = useChatStore((s) => s.cwd);
  const { setCwd, setRoots } = useProjectStore();

  const projectName = cwd
    ? cwd.replace(/\\/g, "/").split("/").pop() ?? "KRYTH"
    : "Open folder…";

  const openFolder = async () => {
    try {
      const path = await invoke<string | null>("open_folder_dialog");
      if (path) {
        const entries = await bridge.listFiles(path);
        setCwd(path);
        setRoots(entries);
      }
    } catch {/* Tauri not available in dev */ }
  };

  return (
    <header className="drag-region h-10 flex items-center gap-1 px-2 shrink-0 border-b border-[rgba(255,255,255,0.05)] bg-chrome">
      {/* macOS window controls spacer */}
      <div className="w-[70px] shrink-0 hidden mac:block" />

      {/* Sidebar toggle */}
      <button
        onClick={toggleSidebar}
        className="no-drag p-1.5 rounded-md text-subtle hover:text-text transition-colors duration-100"
        title="Toggle sidebar"
      >
        <PanelLeft size={14} />
      </button>

      {/* KRYTH wordmark */}
      <span className="no-drag text-xs font-semibold tracking-widest text-subtle select-none px-1">
        KRYTH
      </span>

      <div className="no-drag h-3.5 w-px bg-[rgba(255,255,255,0.06)] mx-0.5" />

      {/* Project folder picker */}
      <button
        onClick={openFolder}
        className="no-drag flex items-center gap-1.5 px-2 h-6 rounded-md text-muted hover:text-text hover:bg-surface3 transition-colors duration-100"
        title={cwd || "Open project folder"}
      >
        <span className="text-xs truncate max-w-[140px]">{projectName}</span>
        <ChevronDown size={10} className="opacity-50" />
      </button>

      <div className="flex-1" />

      {/* Running indicator */}
      {status !== "idle" && (
        <div className="no-drag flex items-center gap-1.5 px-2 h-6">
          <Loader2 size={11} className="animate-spin-slow text-accent" />
          <span className="text-xs text-accent">
            {status === "thinking" ? "Thinking" : "Running"}
          </span>
        </div>
      )}

      {/* Search / palette */}
      <button
        onClick={openPalette}
        className="no-drag flex items-center gap-1.5 px-2.5 h-6 rounded-md text-subtle hover:text-text hover:bg-surface3 transition-colors duration-100 border border-[rgba(255,255,255,0.06)]"
        title="Command palette (Ctrl+K)"
      >
        <Search size={11} />
        <span className="text-xs text-subtle">Search</span>
        <kbd className="ml-0.5 text-[9px] px-1 py-0.5 rounded bg-[rgba(255,255,255,0.04)] font-mono text-subtle">⌃K</kbd>
      </button>

      {/* Model */}
      <ModelChip />

      {/* Connection indicator */}
      <div
        className={cn(
          "w-1.5 h-1.5 rounded-full",
          connStatus === "connected"    && "bg-success",
          connStatus === "disconnected" && "bg-error",
          connStatus === "connecting"   && "bg-warning animate-pulse-soft",
        )}
        title={connStatus}
      />

      {/* Inspector toggle */}
      <button
        onClick={toggleInspector}
        className="no-drag p-1.5 rounded-md text-subtle hover:text-text transition-colors duration-100"
        title="Toggle inspector"
      >
        <PanelRight size={14} />
      </button>

      {/* Settings */}
      <button
        onClick={() => useUIStore.getState().setWorkspaceTab("settings")}
        className="no-drag p-1.5 rounded-md text-subtle hover:text-text transition-colors duration-100"
        title="Settings"
      >
        <Settings size={14} />
      </button>
    </header>
  );
});

const ModelChip = memo(function ModelChip() {
  const [model, setModel] = React.useState("…");

  React.useEffect(() => {
    bridge.getConfig().then((cfg) => {
      const m = cfg["KRYTH_MAIN_MODEL"];
      if (m) setModel(m.split("/").pop()?.slice(0, 18) ?? m);
    }).catch(() => {});
  }, []);

  return (
    <div className="no-drag flex items-center gap-1 px-2 h-6 rounded-md border border-[rgba(255,255,255,0.06)] text-subtle">
      <span className="text-xs truncate max-w-[100px]">{model}</span>
    </div>
  );
});
