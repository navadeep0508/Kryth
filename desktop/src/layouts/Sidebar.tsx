import { memo, Suspense, useState, useEffect, useCallback } from "react";
import React from "react";
import { MessageSquare, FolderOpen, Plug, Brain, Bot, Settings, Plus, Clock, Loader2, FolderPlus, Globe, Search, ChevronRight, ChevronDown, FileCode, Terminal as TerminalIcon, GitBranch } from "lucide-react";
import { cn } from "@/lib/utils";
import { bridge } from "@/lib/krythBridge";
import { useUIStore, type SideTab } from "@/store/uiStore";
import { useWorkspaceStore } from "@/store/workspaceStore";
import { useProjectStore } from "@/store/projectStore";

const FileExplorer = React.lazy(() => import("@/features/explorer/FileExplorer"));

const TABS: { id: SideTab; icon: React.ElementType; label: string }[] = [
  { id: "chats",  icon: MessageSquare, label: "Sessions" },
  { id: "files",  icon: FolderOpen,    label: "Files" },
  { id: "tools",  icon: Plug,          label: "Tools" },
  { id: "memory", icon: Brain,         label: "Memory" },
  { id: "agents", icon: Bot,           label: "Agents" },
  { id: "browser",icon: Globe,         label: "Browser" },
];

export const Sidebar = memo(function Sidebar() {
  const { sideTab, setSideTab, setCenterView, centerView } = useUIStore();

  return (
    <div className="flex h-full w-full bg-surface">
      {/* Icon rail */}
      <div className="w-9 flex flex-col items-center py-2 gap-0.5 border-r border-border shrink-0">
        {TABS.map((tab) => {
          const Icon = tab.icon;
          const active = sideTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setSideTab(tab.id)}
              className={cn(
                "relative w-7 h-7 flex items-center justify-center transition-colors duration-100",
                active
                  ? "text-text bg-panel"
                  : "text-dim hover:text-muted hover:bg-panel-hover"
              )}
              title={tab.label}
            >
              <Icon size={13} />
            </button>
          );
        })}
        <div className="flex-1" />
        <button
          onClick={() => setCenterView(centerView === "settings" ? "chat" : "settings")}
          className={cn(
            "w-7 h-7 flex items-center justify-center transition-colors duration-100",
            centerView === "settings" ? "text-text bg-panel" : "text-dim hover:text-muted hover:bg-panel-hover"
          )}
          title="Settings"
        >
          <Settings size={13} />
        </button>
      </div>

      {/* Panel content */}
      <div className="flex-1 flex flex-col overflow-hidden text-xs">
        {sideTab === "chats" && <ChatsPanel />}
        {sideTab === "files" && <FilesPanel />}
        {sideTab === "tools" && <ToolsPanel />}
        {sideTab === "memory" && <MemoryPanel />}
        {sideTab === "agents" && <AgentsPanel />}
        {sideTab === "browser" && <BrowserPanel />}
      </div>
    </div>
  );
});

/* ── Section header ──────────────────────────────────────────── */
function SectionHeader({ title, count, action }: { title: string; count?: number; action?: { icon: React.ElementType; onClick: () => void; title: string } }) {
  return (
    <div className="flex items-center h-7 px-2 border-b border-border shrink-0">
      <span className="text-[10px] font-semibold text-dim uppercase tracking-wider">{title}</span>
      {count != null && <span className="ml-1.5 text-[10px] text-faint font-mono">{count}</span>}
      <div className="flex-1" />
      {action && (
        <button
          onClick={action.onClick}
          className="p-0.5 text-dim hover:text-muted transition-colors"
          title={action.title}
        >
          <action.icon size={11} />
        </button>
      )}
    </div>
  );
}

/* ── Chats panel ────────────────────────────────────────────── */
interface Session { id: string; project_path: string; updated_at: string }

function relativeTime(dateStr: string): string {
  const diffMs = Date.now() - new Date(dateStr).getTime();
  if (diffMs < 0) return "now";
  const s = Math.floor(diffMs / 1000);
  if (s < 60) return "now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

function projectName(p: string): string {
  return p.replace(/\\/g, "/").split("/").filter(Boolean).pop() || p;
}

function ChatsPanel() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const fetchSessions = useCallback(async () => {
    setLoading(true); setError(null);
    try { const d = await bridge.getSessions(); setSessions(d.sessions); }
    catch (e) { setError(e instanceof Error ? e.message : "Failed"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchSessions(); }, [fetchSessions]);

  const handleNew = () => useWorkspaceStore.getState().clearAll();
  const handleClick = useCallback(async (session: Session) => {
    const store = useWorkspaceStore.getState();
    store.clearAll();
    try {
      const { events } = await bridge.getSessionHistory(session.id);
      if (events?.length) {
        const tid = store.startTurn();
        for (const e of events) store.pushEvent(tid, e as any);
        store.endTurn(tid, "done");
      }
      if (session.project_path) {
        store.setCwd(session.project_path);
        useProjectStore.getState().openFolder(session.project_path);
      }
    } catch { /* skip */ }
  }, []);

  return (
    <>
      <SectionHeader title="Sessions" count={sessions.length} action={{ icon: Plus, onClick: handleNew, title: "New session" }} />
      <div className="flex-1 overflow-y-auto">
        <button
          onClick={handleNew}
          className="w-full flex items-center gap-1.5 px-2 py-1.5 text-dim hover:text-muted hover:bg-panel-hover transition-colors border-b border-border"
        >
          <Plus size={11} />
          <span className="text-[11px]">New session</span>
        </button>
        {loading && <div className="flex justify-center py-4"><Loader2 size={12} className="animate-spin text-dim" /></div>}
        {!loading && error && <p className="text-[11px] text-danger px-2 py-2">{error}</p>}
        {!loading && !error && sessions.length === 0 && <p className="text-[11px] text-dim px-2 py-3">No sessions yet</p>}
        {!loading && !error && sessions.map((s) => (
          <button
            key={s.id}
            onClick={() => handleClick(s)}
            className="w-full flex items-center gap-1.5 px-2 py-1.5 hover:bg-panel-hover transition-colors text-left group border-b border-border/50"
          >
            <MessageSquare size={10} className="text-dim shrink-0" />
            <span className="flex-1 text-[11px] text-muted group-hover:text-text truncate">{projectName(s.project_path)}</span>
            <span className="text-[9px] text-faint shrink-0">{relativeTime(s.updated_at)}</span>
          </button>
        ))}
      </div>
    </>
  );
}

/* ── Files panel ────────────────────────────────────────────── */
async function handleOpenFolder() {
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    const path = await invoke<string | null>("open_folder_dialog");
    if (path) {
      await useProjectStore.getState().openFolder(path);
      useWorkspaceStore.getState().setCwd(path);
    }
  } catch { console.warn("[KRYTH] Tauri invoke unavailable"); }
}

function FilesPanel() {
  return (
    <>
      <SectionHeader title="Files" action={{ icon: FolderPlus, onClick: handleOpenFolder, title: "Open folder" }} />
      <div className="flex-1 overflow-hidden">
        <Suspense fallback={<div className="flex justify-center py-4"><Loader2 size={12} className="animate-spin text-dim" /></div>}>
          <FileExplorer />
        </Suspense>
      </div>
    </>
  );
}

/* ── Browser panel ──────────────────────────────────────────── */
function BrowserPanel() {
  return (
    <div className="flex-1 flex items-center justify-center text-dim text-[11px]">
      Browser panel
    </div>
  );
}

/* ── Tools panel ────────────────────────────────────────────── */
interface ToolInfo { name: string; description: string; source: "builtin" | "mcp" }

function ToolsPanel() {
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const fetchTools = useCallback(async () => {
    setLoading(true); setError(null);
    try { const d = await bridge.getTools(); setTools(d.tools); }
    catch (e) { setError(e instanceof Error ? e.message : "Failed"); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { fetchTools(); }, [fetchTools]);

  const builtin = tools.filter((t) => t.source === "builtin");
  const mcp = tools.filter((t) => t.source === "mcp");

  return (
    <>
      <SectionHeader title="Tools" count={tools.length} />
      <div className="flex-1 overflow-y-auto">
        {loading && <div className="flex justify-center py-4"><Loader2 size={12} className="animate-spin text-dim" /></div>}
        {!loading && error && <p className="text-[11px] text-danger px-2 py-2">{error}</p>}
        {!loading && !error && tools.length === 0 && <p className="text-[11px] text-dim px-2 py-3">No tools</p>}
        {builtin.length > 0 && (
          <div className="mb-2">
            <div className="px-2 py-1 text-[9px] text-faint uppercase tracking-wider font-medium">Built-in ({builtin.length})</div>
            {builtin.map((t) => (
              <div key={t.name} className="px-2 py-1 hover:bg-panel-hover">
                <div className="text-[11px] text-text font-medium">{t.name}</div>
                <div className="text-[10px] text-dim truncate">{t.description}</div>
              </div>
            ))}
          </div>
        )}
        {mcp.length > 0 && (
          <div>
            <div className="px-2 py-1 text-[9px] text-faint uppercase tracking-wider font-medium">MCP ({mcp.length})</div>
            {mcp.map((t) => (
              <div key={t.name} className="px-2 py-1 hover:bg-panel-hover">
                <div className="text-[11px] text-text font-medium">{t.name}</div>
                <div className="text-[10px] text-dim truncate">{t.description}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}

/* ── Memory panel ───────────────────────────────────────────── */
interface MemoryEntry { id: string; content: string; source: string; ts: string }

function MemoryPanel() {
  const [entries, setEntries] = useState<MemoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const fetchMemory = useCallback(async () => {
    setLoading(true); setError(null);
    try { const d = await bridge.getMemory(); setEntries(d.entries); }
    catch (e) { setError(e instanceof Error ? e.message : "Failed"); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { fetchMemory(); }, [fetchMemory]);
  const handleDelete = useCallback(async (id: string) => {
    try { await bridge.deleteMemory(id); setEntries((p) => p.filter((e) => e.id !== id)); }
    catch { /* ignore */ }
  }, []);

  return (
    <>
      <SectionHeader title="Memory" count={entries.length} />
      <div className="flex-1 overflow-y-auto">
        {loading && <div className="flex justify-center py-4"><Loader2 size={12} className="animate-spin text-dim" /></div>}
        {!loading && error && <p className="text-[11px] text-danger px-2 py-2">{error}</p>}
        {!loading && !error && entries.length === 0 && <p className="text-[11px] text-dim px-2 py-3">No memory entries</p>}
        {entries.map((e) => (
          <div key={e.id} className="group px-2 py-1.5 hover:bg-panel-hover border-b border-border/50">
            <div className="flex items-center gap-1">
              <Brain size={9} className="text-dim shrink-0" />
              <span className="text-[9px] text-faint uppercase tracking-wider flex-1">{e.source}</span>
              <button onClick={() => handleDelete(e.id)} className="opacity-0 group-hover:opacity-100 text-[9px] text-dim hover:text-danger">x</button>
            </div>
            <p className="text-[10px] text-muted leading-relaxed mt-0.5">{e.content}</p>
          </div>
        ))}
      </div>
    </>
  );
}

/* ── Agents panel ───────────────────────────────────────────── */
import type { AgentEvent } from "@/store/workspaceStore";

function AgentsPanel() {
  const { turns, activeTurnId } = useWorkspaceStore();
  const activeTurn = turns.find((t) => t.id === activeTurnId);
  const agentEvents = (activeTurn?.events.filter((e): e is AgentEvent => e.type === "agent_update")) ?? [];

  return (
    <>
      <SectionHeader title="Agents" count={agentEvents.length} />
      <div className="flex-1 overflow-y-auto">
        {agentEvents.length === 0 ? (
          <p className="text-[11px] text-dim px-2 py-3">No active agents</p>
        ) : (
          agentEvents.map((evt) => (
            <div key={evt.id} className="px-2 py-1.5 border-b border-border/50">
              <div className="flex items-center gap-1.5">
                <Bot size={10} className={cn("shrink-0", evt.status === "running" ? "text-accent" : "text-dim")} />
                <span className="text-[11px] text-text font-medium truncate flex-1">{evt.name}</span>
                <span className={cn(
                  "text-[9px] px-1",
                  evt.status === "running" && "text-accent",
                  evt.status === "success" && "text-success",
                  evt.status === "failed" && "text-danger",
                  evt.status === "queued" && "text-warning",
                )}>{evt.status}</span>
              </div>
              <p className="text-[10px] text-dim truncate mt-0.5">{evt.task}</p>
            </div>
          ))
        )}
      </div>
    </>
  );
}
