import React, { memo, lazy, Suspense, useMemo } from "react";
import {
  MessageSquare, FolderOpen, Search, Terminal, FileText, Plus, Clock, File,
} from "lucide-react";
import Fuse from "fuse.js";
import { cn } from "@/lib/utils";
import { useUIStore, type SideActivity } from "@/store/uiStore";
import { useChatStore } from "@/store/chatStore";
import { useProjectStore } from "@/store/projectStore";
import { useEditorStore } from "@/store/editorStore";
import { bridge, request } from "@/lib/krythBridge";

const FileExplorer = lazy(() => import("@/features/explorer/FileExplorer"));

// ── Activity definitions ──────────────────────────────────────────────────────

const ACTIVITIES: { id: SideActivity; icon: React.ReactNode; label: string }[] = [
  { id: "chat",     icon: <MessageSquare size={16} />, label: "Chats" },
  { id: "explorer", icon: <FolderOpen size={16} />,    label: "Explorer" },
  { id: "search",   icon: <Search size={16} />,        label: "Search" },
];

// ── Root ──────────────────────────────────────────────────────────────────────

export const Sidebar = memo(function Sidebar() {
  const { sideActivity, toggleSideActivity, openDrawer } = useUIStore();

  return (
    <div className="flex h-full shrink-0">
      {/* Activity bar — always 44px */}
      <div className="w-11 flex flex-col items-center py-1.5 gap-0.5 border-r border-[rgba(255,255,255,0.05)] bg-surface shrink-0">
        {ACTIVITIES.map((a) => (
          <ActivityBtn
            key={a.id}
            label={a.label}
            active={sideActivity === a.id}
            onClick={() => toggleSideActivity(a.id)}
          >
            {a.icon}
          </ActivityBtn>
        ))}

        <div className="flex-1" />

        <ActivityBtn label="Terminal" active={false} onClick={() => openDrawer("terminal")}>
          <Terminal size={16} />
        </ActivityBtn>
        <ActivityBtn label="Logs" active={false} onClick={() => openDrawer("logs")}>
          <FileText size={16} />
        </ActivityBtn>
      </div>

      {/* Side panel — 220px collapsible */}
      <div
        className={cn(
          "flex flex-col border-r border-[rgba(255,255,255,0.05)] bg-surface overflow-hidden",
          "transition-[width] duration-150 ease-out",
          sideActivity ? "w-[220px]" : "w-0"
        )}
      >
        <div className="flex flex-col h-full min-w-[220px]">
          {sideActivity === "chat"     && <ChatPanel />}
          {sideActivity === "explorer" && <ExplorerPanel />}
          {sideActivity === "search"   && <SearchPanel />}
        </div>
      </div>
    </div>
  );
});

// ── Activity button ───────────────────────────────────────────────────────────

const ActivityBtn = memo(function ActivityBtn({
  children, label, active, onClick,
}: {
  children: React.ReactNode; label: string; active: boolean; onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      title={label}
      className={cn(
        "relative w-9 h-9 flex items-center justify-center rounded-lg transition-colors duration-100",
        active ? "text-text bg-[rgba(255,255,255,0.06)]" : "text-subtle hover:text-muted hover:bg-[rgba(255,255,255,0.04)]"
      )}
    >
      {active && (
        <span className="absolute left-0 top-2.5 bottom-2.5 w-0.5 -ml-1 rounded-r-full bg-accent" />
      )}
      {children}
    </button>
  );
});

// ── Panel header ─────────────────────────────────────────────────────────────

function PanelHeader({ label }: { label: string }) {
  return (
    <div className="h-8 flex items-center px-3 shrink-0">
      <span className="text-[10px] font-semibold text-subtle uppercase tracking-widest">{label}</span>
    </div>
  );
}

// ── Chat panel ────────────────────────────────────────────────────────────────

interface RecentSession { id: string; project_path: string; updated_at: string; }

function ChatPanel() {
  const clearMessages   = useChatStore((s) => s.clearMessages);
  const setWorkspaceTab = useUIStore((s) => s.setWorkspaceTab);
  const [sessions, setSessions] = React.useState<RecentSession[]>([]);

  React.useEffect(() => {
    request<{ sessions: RecentSession[] }>("GET", "/api/sessions")
      .catch(() => ({ sessions: [] as RecentSession[] }))
      .then((r) => setSessions(r.sessions?.slice(0, 15) ?? []));
  }, []);

  return (
    <>
      <PanelHeader label="Chats" />
      <div className="px-2 pb-1">
        <button
          onClick={() => { clearMessages(); setWorkspaceTab("chat"); }}
          className="w-full flex items-center gap-2 px-2 h-7 rounded-md border border-dashed border-[rgba(255,255,255,0.08)] hover:border-[rgba(255,255,255,0.14)] hover:bg-[rgba(255,255,255,0.03)] transition-all duration-100 text-xs text-subtle hover:text-muted"
        >
          <Plus size={11} />
          New chat
        </button>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 pb-2 space-y-px">
        {sessions.length > 0 && (
          <>
            <div className="flex items-center gap-1.5 px-2 py-1 mt-1">
              <Clock size={9} className="text-subtle" />
              <span className="text-[9px] font-medium text-subtle uppercase tracking-widest">Recent</span>
            </div>
            {sessions.map((s) => <SessionRow key={s.id} session={s} />)}
          </>
        )}
      </nav>
    </>
  );
}

function SessionRow({ session }: { session: RecentSession }) {
  const name = session.project_path
    ? session.project_path.replace(/\\/g, "/").split("/").pop() ?? "Chat"
    : "Chat";

  const rel = React.useMemo(() => {
    const diff = Date.now() - new Date(session.updated_at).getTime();
    if (diff < 60_000)     return "now";
    if (diff < 3_600_000)  return `${Math.floor(diff / 60_000)}m`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h`;
    return `${Math.floor(diff / 86_400_000)}d`;
  }, [session.updated_at]);

  return (
    <button className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-[rgba(255,255,255,0.04)] transition-colors duration-100 group text-left">
      <MessageSquare size={11} className="text-subtle shrink-0" />
      <span className="flex-1 text-xs text-muted group-hover:text-text truncate">{name}</span>
      <span className="text-[9px] text-subtle/50 shrink-0">{rel}</span>
    </button>
  );
}

// ── Explorer panel ────────────────────────────────────────────────────────────

function ExplorerPanel() {
  return (
    <Suspense fallback={<PanelLoading />}>
      <FileExplorer />
    </Suspense>
  );
}

// ── Search panel ──────────────────────────────────────────────────────────────

function SearchPanel() {
  const [query, setQuery]   = React.useState("");
  const flatNodes           = useProjectStore((s) => s.flatNodes);
  const openTab             = useEditorStore((s) => s.openTab);
  const setWorkspaceTab     = useUIStore((s) => s.setWorkspaceTab);

  const files = useMemo(
    () => flatNodes.filter((n) => !n.is_dir).map((n) => ({ path: n.path, name: n.name })),
    [flatNodes]
  );

  const fuse = useMemo(() => new Fuse(files, { keys: ["name", "path"], threshold: 0.4 }), [files]);

  const results = useMemo(
    () => query.trim() ? fuse.search(query).slice(0, 15).map((r) => r.item) : files.slice(0, 15),
    [query, fuse, files]
  );

  const openFile = React.useCallback(async (f: { path: string; name: string }) => {
    try {
      const content = await bridge.readFile(f.path);
      openTab({ path: f.path, filename: f.name, content, language: "" });
    } catch {
      openTab({ path: f.path, filename: f.name, content: "", language: "" });
    }
    setWorkspaceTab("editor");
  }, [openTab, setWorkspaceTab]);

  return (
    <>
      <PanelHeader label="Search" />
      <div className="px-2 pb-1">
        <input
          type="text"
          value={query}
          autoFocus
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search files…"
          className="w-full h-7 px-2.5 text-xs bg-surface2 border border-[rgba(255,255,255,0.08)] rounded-md text-text placeholder:text-subtle outline-none focus:border-[rgba(255,255,255,0.16)] transition-colors duration-100"
          style={{ userSelect: "text" }}
        />
      </div>
      <div className="flex-1 overflow-y-auto">
        {files.length === 0 ? (
          <p className="px-3 py-2 text-xs text-subtle">Open a folder to search</p>
        ) : results.length === 0 ? (
          <p className="px-3 py-2 text-xs text-subtle">No files match</p>
        ) : (
          results.map((f) => (
            <button
              key={f.path}
              onClick={() => openFile(f)}
              className="w-full flex items-center gap-2 px-2 py-1.5 hover:bg-[rgba(255,255,255,0.04)] transition-colors duration-100 text-left group"
            >
              <File size={10} className="text-subtle shrink-0" />
              <span className="text-xs text-muted group-hover:text-text truncate">{f.name}</span>
              <span className="text-[9px] text-subtle/50 truncate ml-auto shrink-0">
                {f.path.split(/[\\/]/).slice(-2, -1)[0]}
              </span>
            </button>
          ))
        )}
      </div>
    </>
  );
}

function PanelLoading() {
  return <div className="flex-1 flex items-center justify-center text-subtle text-xs">Loading…</div>;
}
