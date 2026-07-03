import { memo, useMemo, useState, useCallback } from "react";
import { Loader2, ChevronDown, ChevronRight, FileCode, Terminal, Search, Globe, Play, Brain, CheckCircle2, Clock, AlertTriangle, Cpu, Zap, FolderOpen, GitBranch, List, X, Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { useUIStore } from "@/store/uiStore";
import { useWorkspaceStore, type ToolCallEvent, type DiffEvent, type ApprovalEvent } from "@/store/workspaceStore";
import { getAgentConfig } from "@/lib/agentRuntime";
import { PipelineProgress } from "@/features/workspace/PipelineProgress";
import { aggregateTools, type SemanticAction } from "@/lib/toolAggregator";

const ACTION_ICONS: Record<string, React.ElementType> = {
  search: Search, file: FileCode, edit: FileCode, terminal: Terminal,
  folder: FolderOpen, list: List, git: GitBranch, bot: Brain, brain: Brain, tool: Play,
};

export const RightPanel = memo(function RightPanel() {
  const { agentStatus, tokenBudget, sessionTokens } = useUIStore();
  const pipelineStages = useWorkspaceStore((s) => s.pipelineStages);
  const activeTurn = useWorkspaceStore((s) => {
    if (!s.activeTurnId) return null;
    return s.turns.find((t) => t.id === s.activeTurnId) ?? null;
  });

  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);

  const allTools = useMemo(() => !activeTurn ? [] : activeTurn.events.filter((e): e is ToolCallEvent => e.type === "tool_call"), [activeTurn]);
  const diffs = useMemo(() => !activeTurn ? [] : activeTurn.events.filter((e): e is DiffEvent => e.type === "diff"), [activeTurn]);
  const approvals = useMemo(() => !activeTurn ? [] : activeTurn.events.filter((e): e is ApprovalEvent => e.type === "approval_required" && !e.resolved), [activeTurn]);

  const semanticActions = useMemo(() => aggregateTools(allTools), [allTools]);
  const activeActions = semanticActions.filter((a) => a.status === "running");
  const completedActions = semanticActions.filter((a) => a.status === "done" || a.status === "mixed" || a.status === "failed");

  const hasProgress = pipelineStages.some((s) => s.status !== "pending");
  const hasContext = diffs.length > 0 || allTools.some((t) => t.tool === "read_file");

  const activeFiles = useMemo(() => {
    if (!activeTurn) return [];
    const files = new Set<string>();
    for (const e of activeTurn.events) {
      if (e.type === "diff") files.add((e as DiffEvent).path);
      if (e.type === "tool_call") {
        const tc = e as ToolCallEvent;
        const p = tc.args.path ?? tc.args.file_path ?? "";
        if (typeof p === "string" && p) files.add(p);
      }
    }
    return [...files].slice(0, 5);
  }, [activeTurn]);

  const repoSize = useMemo(() => {
    try {
      const stored = localStorage.getItem("kryth:project-stats");
      if (stored) { const p = JSON.parse(stored); return { files: p.files ?? 0, symbols: p.symbols ?? 0 }; }
    } catch {}
    return null;
  }, []);

  return (
    <div className="flex flex-col h-full bg-surface text-xs select-none overflow-hidden">
      <div className="flex-1 overflow-y-auto">
        {/* ── Pipeline stages ─────────────────────────────── */}
        <div className="p-2">
          <div className="text-[10px] font-semibold text-dim uppercase tracking-wider mb-1 px-1">Pipeline</div>
          <PipelineProgress stages={pipelineStages} />
        </div>

        <div className="border-t border-border mx-2" />

        {/* ── Context ─────────────────────────────────────── */}
        <div className="p-2">
          <div className="flex items-center gap-1 mb-1 px-1">
            <Brain size={10} className="text-dim" />
            <span className="text-[10px] font-semibold text-dim uppercase tracking-wider">Context</span>
          </div>
          <div className="space-y-1">
            {activeFiles.length > 0 && (
              <div className="px-2 py-1.5 bg-panel border border-border">
                <div className="flex items-center justify-between mb-0.5">
                  <span className="text-[9px] text-faint uppercase tracking-wider">Files</span>
                  <span className="text-[9px] text-faint font-mono">{activeFiles.length}</span>
                </div>
                {activeFiles.map((f) => (
                  <div key={f} className="flex items-center gap-1 py-0.5">
                    <FileCode size={8} className="text-dim shrink-0" />
                    <span className="text-[10px] text-muted font-mono truncate">{f.replace(/\\/g, "/").split("/").pop()}</span>
                  </div>
                ))}
              </div>
            )}
            {repoSize && (
              <div className="flex items-center gap-2 px-2 py-1 bg-panel border border-border">
                <span className="flex items-center gap-1 text-dim"><FolderOpen size={9} /><span className="text-[9px]">{repoSize.files} files</span></span>
                <span className="flex items-center gap-1 text-dim"><List size={9} /><span className="text-[9px]">{repoSize.symbols} sym</span></span>
              </div>
            )}
            {!hasContext && !repoSize && <p className="text-[10px] text-faint px-2">Waiting for context…</p>}
          </div>
        </div>

        <div className="border-t border-border mx-2" />

        {/* ── Progress / Semantic actions ────────────────── */}
        <div className="p-2">
          <div className="flex items-center gap-1 mb-1 px-1">
            <Zap size={10} className="text-dim" />
            <span className="text-[10px] font-semibold text-dim uppercase tracking-wider">Activity</span>
          </div>
          {hasProgress ? (
            <div className="bg-panel border border-border px-2 py-1.5">
              {activeActions.length > 0 && (
                <div className="space-y-0.5 mb-1">
                  {activeActions.map((a) => <SemanticRow key={a.id} action={a} running />)}
                </div>
              )}
              {completedActions.length > 0 && (
                <div className="space-y-0.5">
                  {completedActions.map((a) => <SemanticRow key={a.id} action={a} />)}
                </div>
              )}
              {activeActions.length === 0 && completedActions.length === 0 && (
                <p className="text-[10px] text-faint">No activity yet</p>
              )}
            </div>
          ) : (
            <p className="text-[10px] text-faint px-2">Waiting for task…</p>
          )}
        </div>

        {/* ── Approvals ───────────────────────────────────── */}
        {approvals.length > 0 && (
          <>
            <div className="border-t border-border mx-2" />
            <div className="p-2">
              <div className="flex items-center gap-1 mb-1 px-1">
                <AlertTriangle size={10} className="text-warning" />
                <span className="text-[10px] font-semibold text-dim uppercase tracking-wider">Approvals</span>
              </div>
              {approvals.map((a) => (
                <div key={a.id} className="flex items-start gap-1.5 px-2 py-1.5 bg-panel border border-border">
                  <AlertTriangle size={9} className="text-warning mt-0.5 shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-[10px] text-muted truncate">{a.message}</p>
                    <span className={cn("text-[9px] font-medium", a.risk === "high" ? "text-danger" : a.risk === "medium" ? "text-warning" : "text-dim")}>
                      {a.risk}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}

        {/* ── Diagnostics (collapsible) ──────────────────── */}
        <div className="border-t border-border mx-2" />
        <div className="p-2">
          <button
            onClick={() => setDiagnosticsOpen(!diagnosticsOpen)}
            className="flex items-center gap-1 w-full px-1 py-0.5 hover:bg-panel-hover transition-colors"
          >
            {diagnosticsOpen ? <ChevronDown size={9} className="text-dim" /> : <ChevronRight size={9} className="text-dim" />}
            <Cpu size={9} className="text-dim" />
            <span className="text-[10px] font-semibold text-dim uppercase tracking-wider">Diagnostics</span>
          </button>
          {diagnosticsOpen && (
            <div className="mt-1.5 space-y-1.5">
              {/* Token Usage */}
              <div className="bg-panel border border-border px-2 py-1.5">
                {tokenBudget && (
                  <div className="mb-1.5">
                    <div className="flex items-center justify-between mb-0.5">
                      <span className="text-[9px] text-dim uppercase tracking-wider">Budget</span>
                      <span className="text-[9px] text-muted font-mono">{tokenBudget.used.toLocaleString()} / {tokenBudget.limit.toLocaleString()}</span>
                    </div>
                    <div className="h-1 bg-panel-hover overflow-hidden">
                      <div className={cn("h-full transition-all duration-300", tokenBudget.remaining / tokenBudget.limit < 0.1 ? "bg-danger" : tokenBudget.remaining / tokenBudget.limit < 0.25 ? "bg-warning" : "bg-accent")} style={{ width: `${(tokenBudget.used / tokenBudget.limit) * 100}%` }} />
                    </div>
                    <span className="text-[9px] text-dim mt-0.5 block">{tokenBudget.remaining.toLocaleString()} remaining</span>
                  </div>
                )}
                {sessionTokens.total > 0 && (
                  <div className="flex gap-1">
                    <TokenStat label="Prompt" value={sessionTokens.prompt.toLocaleString()} />
                    <TokenStat label="Comp" value={sessionTokens.completion.toLocaleString()} />
                    <TokenStat label="Total" value={sessionTokens.total.toLocaleString()} />
                  </div>
                )}
                {!tokenBudget && sessionTokens.total === 0 && <p className="text-[9px] text-faint">No usage yet</p>}
              </div>

              {/* Provider */}
              <div className="bg-panel border border-border px-2 py-1.5 space-y-0.5">
                <div className="flex items-center justify-between">
                  <span className="text-[9px] text-dim">Provider</span>
                  <span className="text-[9px] text-muted capitalize">{getAgentConfig().provider}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[9px] text-dim">Model</span>
                  <span className="text-[9px] text-muted font-mono">{getAgentConfig().model}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[9px] text-dim">Status</span>
                  <StatusBadge status={agentStatus} />
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
});

/* ── Semantic action row ────────────────────────────────────── */
function SemanticRow({ action, running }: { action: SemanticAction; running?: boolean }) {
  const Icon = ACTION_ICONS[action.icon] ?? Play;
  return (
    <div className={cn("flex items-center gap-1.5", running && "text-accent")}>
      {running ? (
        <Loader2 size={8} className="animate-spin shrink-0" />
      ) : (
        <span className={cn("w-1 h-1 shrink-0", action.status === "done" && "bg-success", action.status === "failed" && "bg-danger", action.status === "mixed" && "bg-warning")} />
      )}
      <Icon size={8} className="shrink-0 text-dim" />
      <span className="text-[10px] flex-1 truncate">{action.label}</span>
      {action.count > 1 && <span className="text-[8px] text-faint font-mono">x{action.count}</span>}
      {action.runtime > 0 && !running && <span className="text-[8px] text-faint font-mono">{action.runtime.toFixed(1)}s</span>}
    </div>
  );
}

function TokenStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex-1 flex flex-col items-center bg-panel-hover p-1">
      <span className="text-[8px] text-dim uppercase tracking-wider">{label}</span>
      <span className="text-[10px] font-mono text-text font-medium">{value}</span>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const colorMap: Record<string, string> = {
    idle: "text-dim", thinking: "text-accent", planning: "text-accent",
    executing: "text-success", editing: "text-warning",
    waiting_approval: "text-warning", done: "text-success", error: "text-danger",
  };
  return <span className={cn("text-[9px] font-medium", colorMap[status] ?? "text-dim")}>{status.replace("_", " ")}</span>;
}
