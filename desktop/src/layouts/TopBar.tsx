import React, { memo } from "react";
import {
  PanelLeft, PanelRight, Settings, ChevronDown,
  Cpu, GitBranch, FolderOpen, Search,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useUIStore } from "@/store/uiStore";
import { useChatStore } from "@/store/chatStore";
import { bridge } from "@/lib/krythBridge";

const STATUS_DOT: Record<string, string> = {
  connected:    "bg-success",
  disconnected: "bg-error",
  connecting:   "bg-warning animate-pulse-soft",
};

export const TopBar = memo(function TopBar() {
  const { toggleSidebar, toggleInspector, openPalette, connStatus } = useUIStore();
  const status = useChatStore((s) => s.status);
  const cwd    = useChatStore((s) => s.cwd);

  const projectName = cwd
    ? cwd.replace(/\\/g, "/").split("/").pop() ?? "KRYTH"
    : "KRYTH";

  return (
    <header
      className={cn(
        "drag-region h-11 flex items-center gap-2 px-3",
        "border-b border-border bg-surface",
        "shrink-0 z-40"
      )}
    >
      {/* Spacer for window controls on macOS */}
      <div className="w-[72px] shrink-0 hidden mac:block" />

      {/* Sidebar toggle */}
      <button
        onClick={toggleSidebar}
        className="no-drag p-1.5 rounded-md text-muted hover:text-text hover:bg-surface2 transition-colors duration-120"
        title="Toggle sidebar"
      >
        <PanelLeft size={15} />
      </button>

      {/* Project name */}
      <button
        className="no-drag flex items-center gap-1.5 px-2.5 h-7 rounded-lg bg-surface2 border border-border hover:border-accent/40 transition-colors duration-120"
        title={cwd || "No project open"}
      >
        <FolderOpen size={12} className="text-accent" />
        <span className="text-sm font-medium text-text truncate max-w-[140px]">
          {projectName}
        </span>
        <ChevronDown size={11} className="text-muted" />
      </button>

      {/* Branch pill */}
      <div className="no-drag flex items-center gap-1 px-2 h-7 rounded-lg bg-surface2 border border-border">
        <GitBranch size={12} className="text-muted" />
        <span className="text-xs text-muted">main</span>
      </div>

      <div className="flex-1" />

      {/* Search / command palette */}
      <button
        onClick={openPalette}
        className="no-drag flex items-center gap-2 px-3 h-7 rounded-lg bg-surface2 border border-border hover:border-accent/40 transition-colors duration-120 text-muted hover:text-text"
        title="Command Palette (Ctrl+K)"
      >
        <Search size={12} />
        <span className="text-xs">Search…</span>
        <kbd className="ml-1 text-[10px] px-1 py-0.5 rounded bg-bg border border-border font-mono">
          ⌃K
        </kbd>
      </button>

      {/* Model pill */}
      <ModelPill />

      {/* Agent running indicator */}
      {status !== "idle" && (
        <div className="no-drag flex items-center gap-1.5 px-2.5 h-7 rounded-lg bg-accent/10 border border-accent/20">
          <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse-soft" />
          <span className="text-xs text-accent font-medium">
            {status === "thinking" ? "Thinking…" : "Running…"}
          </span>
        </div>
      )}

      {/* Connection dot */}
      <div
        className={cn("w-2 h-2 rounded-full", STATUS_DOT[connStatus] ?? "bg-muted")}
        title={connStatus}
      />

      {/* Inspector toggle */}
      <button
        onClick={toggleInspector}
        className="no-drag p-1.5 rounded-md text-muted hover:text-text hover:bg-surface2 transition-colors duration-120"
        title="Toggle inspector"
      >
        <PanelRight size={15} />
      </button>

      {/* Settings */}
      <a
        href="/settings"
        className="no-drag p-1.5 rounded-md text-muted hover:text-text hover:bg-surface2 transition-colors duration-120"
        title="Settings"
      >
        <Settings size={15} />
      </a>
    </header>
  );
});

const ModelPill = memo(function ModelPill() {
  const [model, setModel] = React.useState("…");

  React.useEffect(() => {
    bridge.getConfig().then((cfg) => {
      const m = cfg["KRYTH_MAIN_MODEL"];
      if (m) setModel(m.split("/").pop()?.slice(0, 20) ?? m);
    }).catch(() => {});
  }, []);

  return (
    <div className="no-drag flex items-center gap-1 px-2 h-7 rounded-lg bg-surface2 border border-border">
      <Cpu size={11} className="text-muted" />
      <span className="text-xs text-muted truncate max-w-[100px]">{model}</span>
    </div>
  );
});
