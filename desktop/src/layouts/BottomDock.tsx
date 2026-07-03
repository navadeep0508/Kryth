import { memo, lazy, Suspense, useState, useEffect, useCallback, useRef } from "react";
import { Terminal, ScrollText, Bug, X, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import { useUIStore, type DockTab } from "@/store/uiStore";
import { bridge } from "@/lib/krythBridge";
import { useWorkspaceStore, type ToolCallEvent } from "@/store/workspaceStore";

const TerminalPanel = lazy(() => import("@/features/terminal/TerminalPanel"));

const DOCK_TABS: { id: DockTab; icon: React.ElementType; label: string }[] = [
  { id: "terminal", icon: Terminal,   label: "Terminal" },
  { id: "logs",     icon: ScrollText, label: "Logs" },
  { id: "debug",    icon: Bug,        label: "Debug" },
];

export const BottomDock = memo(function BottomDock() {
  const { dockTab, setDockTab, toggleDock } = useUIStore();

  return (
    <div className="flex flex-col h-full bg-sidebar">
      {/* Tab strip */}
      <div className="h-8 flex items-center px-2 border-b border-border-soft shrink-0">
        {DOCK_TABS.map((tab) => {
          const Icon = tab.icon;
          const active = dockTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setDockTab(tab.id)}
              className={cn(
                "flex items-center gap-1.5 px-2.5 h-6 rounded-sm text-xs transition-colors duration-100",
                active ? "bg-panel text-text" : "text-dim hover:text-muted"
              )}
            >
              <Icon size={11} />
              {tab.label}
            </button>
          );
        })}
        <div className="flex-1" />
        <button
          onClick={toggleDock}
          className="p-1 rounded text-dim hover:text-muted transition-colors duration-100"
          title="Close dock"
        >
          <X size={12} />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {dockTab === "terminal" && (
          <Suspense fallback={<DockLoading />}>
            <TerminalPanel />
          </Suspense>
        )}
        {dockTab === "logs" && <LogsPanel />}
        {dockTab === "debug" && <DebugPanel />}
      </div>
    </div>
  );
});

function DockLoading() {
  return <div className="flex items-center justify-center h-full text-dim text-xs">Loading terminal...</div>;
}

/* ── Logs Panel — fetches from /api/logs and auto-refreshes ─────────────────── */

function LogsPanel() {
  const [lines, setLines] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const data = await bridge.getLogs(200);
      setLines(data.lines);
    } catch {
      setLines(["[info] Log endpoint not available. Agent logs will appear here once the backend supports /api/logs."]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchLogs();
    // Auto-refresh every 3s while the tab is active
    const interval = setInterval(fetchLogs, 3000);
    return () => clearInterval(interval);
  }, [fetchLogs]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [lines]);

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center px-3 py-1 border-b border-border-soft shrink-0">
        <span className="text-2xs text-dim flex-1">Agent output log</span>
        <button
          onClick={fetchLogs}
          className="p-0.5 rounded text-dim hover:text-muted transition-colors duration-100"
          title="Refresh"
        >
          <RefreshCw size={10} />
        </button>
      </div>
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 font-mono text-xs text-muted leading-relaxed">
        {loading && lines.length === 0 && (
          <span className="text-dim">Loading logs...</span>
        )}
        {lines.map((line, i) => (
          <div key={i} className={cn(
            "py-px",
            line.includes("[error]") && "text-danger",
            line.includes("[warn]") && "text-warning",
          )}>
            {line}
          </div>
        ))}
        {!loading && lines.length === 0 && (
          <span className="text-dim">No logs available.</span>
        )}
      </div>
    </div>
  );
}

/* ── Debug Panel — shows tool calls from current turn ──────────────────────── */

function DebugPanel() {
  const turns = useWorkspaceStore((s) => s.turns);

  // Gather all tool_call events across all turns for debugging
  const toolCalls: ToolCallEvent[] = [];
  for (const turn of turns) {
    for (const evt of turn.events) {
      if (evt.type === "tool_call") {
        toolCalls.push(evt);
      }
    }
  }

  const recent = toolCalls.slice(-50);

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center px-3 py-1 border-b border-border-soft shrink-0">
        <span className="text-2xs text-dim flex-1">Debug console — tool calls</span>
        <span className="text-2xs text-faint">{toolCalls.length} calls</span>
      </div>
      <div className="flex-1 overflow-y-auto p-3 font-mono text-xs text-muted space-y-1">
        {recent.length === 0 && (
          <span className="text-dim">No tool calls yet. Run an agent task to see debug output.</span>
        )}
        {recent.map((tc) => (
          <div key={tc.id} className="flex items-start gap-2 py-0.5">
            <span className={cn(
              "text-2xs px-1 rounded shrink-0 mt-px",
              tc.status === "success" && "bg-success/15 text-success",
              tc.status === "failed" && "bg-danger/15 text-danger",
              tc.status === "running" && "bg-accent/15 text-accent",
              tc.status === "queued" && "bg-panel-hover text-dim",
            )}>
              {tc.status}
            </span>
            <span className="text-text font-medium">{tc.tool}</span>
            {tc.runtime != null && (
              <span className="text-faint ml-auto">{tc.runtime}ms</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
