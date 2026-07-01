import { memo, useState, useRef, useEffect } from "react";
import {
  PanelLeftClose, PanelLeftOpen,
  Settings, Terminal, Loader2, CircleDot,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useUIStore, type AgentStatus } from "@/store/uiStore";
import { useProjectStore } from "@/store/projectStore";
import { bridge } from "@/lib/krythBridge";

type ExecMode = "auto" | "fast" | "deep" | "max";

const EXEC_MODE_ORDER: ExecMode[] = ["fast", "auto", "deep", "max"];

const EXEC_MODE_BACKEND: Record<ExecMode, string> = {
  fast: "FAST",
  auto: "BALANCED",
  deep: "MAXIMUM_QUALITY",
  max: "PONYTAIL",
};

const EXEC_MODE_TOOLTIPS: Record<ExecMode, string> = {
  fast: "FAST — Minimal verification, no tests, 8 workers",
  auto: "BALANCED — Standard verification with tests",
  deep: "DEEP — Maximum quality, code review, 16 workers",
  max: "MAX — Ponytail mode: minimal code, maximum elegance",
};

const STATUS_LABELS: Record<AgentStatus, string> = {
  idle: "IDLE",
  thinking: "THINKING",
  planning: "PLANNING",
  executing: "EXECUTING",
  editing: "EDITING",
  waiting_approval: "APPROVAL",
  done: "DONE",
  error: "ERROR",
};

const STATUS_COLORS: Record<AgentStatus, string> = {
  idle: "text-dim",
  thinking: "text-accent",
  planning: "text-accent",
  executing: "text-success",
  editing: "text-warning",
  waiting_approval: "text-warning",
  done: "text-success",
  error: "text-danger",
};

export const TopBar = memo(function TopBar() {
  const {
    sideCollapsed, toggleSidebar,
    toggleDock,
    agentStatus, currentModel, execMode, connStatus,
    setExecMode,
    sessionTokens, tokenBudget,
  } = useUIStore();

  const cwd = useProjectStore((s) => s.cwd);
  const folderName = cwd ? cwd.replace(/\\/g, "/").split("/").pop() : "";

  const isActive = agentStatus !== "idle" && agentStatus !== "done";

  const [showModeDropdown, setShowModeDropdown] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    if (!showModeDropdown) return;
    function handleClick(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowModeDropdown(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [showModeDropdown]);

  const handleModeSelect = (mode: ExecMode) => {
    setExecMode(mode);
    setShowModeDropdown(false);
    // Send to backend
    bridge.patchConfig("KRYTH_EXECUTION_PROFILE", EXEC_MODE_BACKEND[mode]).catch(() => {
      // Silently fail — config update is best-effort
    });
  };

  return (
    <header className="h-10 flex items-center px-3 gap-3 border-b border-border bg-sidebar shrink-0 drag-region">
      {/* Left controls */}
      <div className="flex items-center gap-1 no-drag">
        <button
          onClick={toggleSidebar}
          className="p-1.5 rounded-md text-muted hover:text-text hover:bg-panel-hover transition-colors duration-100"
          title={sideCollapsed ? "Show sidebar" : "Hide sidebar"}
        >
          {sideCollapsed ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}
        </button>
      </div>

      {/* Branding + status */}
      <div className="flex items-center gap-2 no-drag">
        <span className="text-sm font-semibold text-text tracking-tight">KRYTH</span>
        {folderName && (
          <>
            <span className="text-2xs text-dim">/</span>
            <span className="text-xs text-muted truncate max-w-[120px]" title={cwd}>{folderName}</span>
          </>
        )}
        <span className="text-2xs text-dim">|</span>
        <span className="text-xs text-muted font-mono">{currentModel}</span>
        <span className="text-2xs text-dim">|</span>
        <div className="relative" ref={dropdownRef}>
          <button
            onClick={() => setShowModeDropdown(!showModeDropdown)}
            className="text-xs text-muted uppercase hover:text-accent transition-colors duration-100 cursor-pointer"
            title={EXEC_MODE_TOOLTIPS[execMode]}
          >
            {execMode}
          </button>
          {showModeDropdown && (
            <div className="absolute top-full left-0 mt-1 z-50 bg-panel border border-border rounded-md shadow-lg py-1 min-w-[200px]">
              {EXEC_MODE_ORDER.map((mode) => (
                <button
                  key={mode}
                  onClick={() => handleModeSelect(mode)}
                  className={cn(
                    "w-full text-left px-3 py-1.5 text-xs hover:bg-panel-hover transition-colors duration-100",
                    mode === execMode ? "text-accent font-medium" : "text-muted"
                  )}
                >
                  <span className="uppercase font-mono">{mode}</span>
                  <span className="ml-2 text-2xs text-dim normal-case">
                    {mode === "fast" && "Minimal verification, no tests, 8 workers"}
                    {mode === "auto" && "Standard verification with tests"}
                    {mode === "deep" && "Maximum quality, code review, 16 workers"}
                    {mode === "max" && "Ponytail mode — minimal code, maximum elegance"}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Live status */}
      <div className="flex items-center gap-1.5 ml-4 no-drag">
        {isActive && <Loader2 size={11} className="animate-spin-slow text-accent" />}
        {!isActive && <CircleDot size={11} className={STATUS_COLORS[agentStatus]} />}
        <span className={cn("text-2xs font-mono font-medium uppercase tracking-wide", STATUS_COLORS[agentStatus])}>
          {STATUS_LABELS[agentStatus]}
        </span>
      </div>

      {/* Session token counter */}
      {sessionTokens.total > 0 && (
        <div className="flex items-center gap-1 ml-3 no-drag" title={`Prompt: ${sessionTokens.prompt.toLocaleString()} | Completion: ${sessionTokens.completion.toLocaleString()}`}>
          <span className="text-2xs text-dim font-mono">
            {sessionTokens.total.toLocaleString()} tok
          </span>
        </div>
      )}

      {/* Token budget bar */}
      {tokenBudget && (
        <div className="flex items-center gap-1.5 ml-2 no-drag" title={`${tokenBudget.used.toLocaleString()} / ${tokenBudget.limit.toLocaleString()} tokens used`}>
          <div className="w-16 h-1.5 rounded-full bg-border-soft overflow-hidden">
            <div
              className={cn(
                "h-full rounded-full transition-all duration-300",
                tokenBudget.remaining / tokenBudget.limit > 0.3 ? "bg-accent/60" :
                tokenBudget.remaining / tokenBudget.limit > 0.1 ? "bg-warning" : "bg-danger"
              )}
              style={{ width: `${Math.min(100, (tokenBudget.used / tokenBudget.limit) * 100)}%` }}
            />
          </div>
          <span className="text-2xs text-dim font-mono">
            {Math.round((tokenBudget.used / tokenBudget.limit) * 100)}%
          </span>
        </div>
      )}

      <div className="flex-1" />

      {/* Connection */}
      <div className="flex items-center gap-1.5 no-drag">
        <div
          className={cn(
            "w-1.5 h-1.5 rounded-full",
            connStatus === "connected" && "bg-success",
            connStatus === "connecting" && "bg-warning animate-pulse-dot",
            connStatus === "disconnected" && "bg-danger",
          )}
        />
        <span className="text-2xs text-dim">{connStatus}</span>
      </div>

      {/* Right controls */}
      <div className="flex items-center gap-0.5 no-drag">
        <button
          onClick={() => toggleDock()}
          className="p-1.5 rounded-md text-muted hover:text-text hover:bg-panel-hover transition-colors duration-100"
          title="Terminal"
        >
          <Terminal size={14} />
        </button>
        <button
          onClick={() => {
            const ui = useUIStore.getState();
            ui.setCenterView(ui.centerView === "settings" ? "chat" : "settings");
          }}
          className="p-1.5 rounded-md text-muted hover:text-text hover:bg-panel-hover transition-colors duration-100"
          title="Settings"
        >
          <Settings size={14} />
        </button>
      </div>
    </header>
  );
});
