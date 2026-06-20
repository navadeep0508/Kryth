import React, { memo, lazy, Suspense } from "react";
import {
  MessageSquare, FolderOpen, Search, Terminal, FileText, Plus, Clock,
} from "lucide-react";

const FileExplorer = lazy(() => import("@/features/explorer/FileExplorer"));
import { cn } from "@/lib/utils";
import { useUIStore, type SideActivity } from "@/store/uiStore";
import { useChatStore } from "@/store/chatStore";
import { request } from "@/lib/krythBridge";

// ── Activity bar items ────────────────────────────────────────────────────────

interface ActivityItem {
  id: SideActivity;
  icon: React.ReactNode;
  label: string;
}

const ACTIVITIES: ActivityItem[] = [
  { id: "chat",     icon: <MessageSquare size={18} />, label: "Chats" },
  { id: "explorer", icon: <FolderOpen size={18} />,    label: "Explorer" },
  { id: "search",   icon: <Search size={18} />,        label: "Search" },
];

// ── Root component ────────────────────────────────────────────────────────────

export const Sidebar = memo(function Sidebar() {
  const { sideActivity, toggleSideActivity, openDrawer } = useUIStore();

  return (
    <div className="flex h-full shrink-0">
      {/* Activity bar — always 48px */}
      <div className="w-12 flex flex-col items-center py-2 gap-0.5 border-r border-border bg-surface shrink-0">
        {ACTIVITIES.map((a) => (
          <ActivityBtn
            key={a.id}
            item={a}
            active={sideActivity === a.id}
            onClick={() => toggleSideActivity(a.id)}
          />
        ))}

        <div className="flex-1" />

        {/* Terminal shortcut */}
        <ActivityBtn
          item={{ id: "chat", icon: <Terminal size={18} />, label: "Terminal" }}
          active={false}
          onClick={() => openDrawer("terminal")}
        />
        <ActivityBtn
          item={{ id: "chat", icon: <FileText size={18} />, label: "Logs" }}
          active={false}
          onClick={() => openDrawer("logs")}
        />
      </div>

      {/* Side panel — 220px collapsible */}
      <div
        className={cn(
          "flex flex-col border-r border-border bg-surface overflow-hidden",
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
  item, active, onClick,
}: {
  item: { id: string; icon: React.ReactNode; label: string };
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      title={item.label}
      className={cn(
        "relative w-10 h-10 flex items-center justify-center rounded-lg",
        "transition-colors duration-120",
        active
          ? "bg-accent/15 text-text"
          : "text-muted hover:text-text hover:bg-surface2"
      )}
    >
      {active && (
        <span className="absolute left-0 top-2 bottom-2 w-0.5 -ml-1 rounded-full bg-accent" />
      )}
      {item.icon}
    </button>
  );
});

// ── Side panel sections ───────────────────────────────────────────────────────

interface RecentSession {
  id: string;
  project_path: string;
  updated_at: string;
}

function ChatPanel() {
  const clearMessages = useChatStore((s) => s.clearMessages);
  const { setWorkspaceTab } = useUIStore();
  const [sessions, setSessions] = React.useState<RecentSession[]>([]);

  React.useEffect(() => {
    request<{ sessions: RecentSession[] }>("GET", "/api/sessions")
      .catch(() => ({ sessions: [] as RecentSession[] }))
      .then((r) => setSessions(r.sessions?.slice(0, 15) ?? []));
  }, []);

  return (
    <>
      <PanelHeader label="Chats" />
      <div className="px-2 pb-2">
        <button
          onClick={() => {
            clearMessages();
            setWorkspaceTab("chat");
          }}
          className="w-full flex items-center gap-2 px-2.5 h-8 rounded-lg border border-dashed border-border hover:border-accent/40 hover:bg-surface2 transition-all duration-120 text-xs text-muted hover:text-text"
        >
          <Plus size={12} />
          New chat
        </button>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 pb-2 space-y-0.5">
        {sessions.length > 0 && (
          <>
            <SectionLabel icon={<Clock size={10} />} label="Recent" />
            {sessions.map((s) => <SessionRow key={s.id} session={s} />)}
          </>
        )}
      </nav>
    </>
  );
}

function ExplorerPanel() {
  return (
    <Suspense fallback={<div className="flex-1 flex items-center justify-center text-muted text-xs">Loading…</div>}>
      <FileExplorer />
    </Suspense>
  );
}

function SearchPanel() {
  const [query, setQuery] = React.useState("");

  return (
    <>
      <PanelHeader label="Search" />
      <div className="px-2 pb-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search files…"
          className="w-full h-7 px-2.5 text-xs bg-surface2 border border-border rounded-md text-text placeholder:text-muted outline-none focus:border-accent/50 transition-colors duration-120"
          style={{ userSelect: "text" }}
        />
      </div>
      <div className="flex-1 overflow-y-auto px-2 text-xs text-muted">
        {!query && <p className="px-2 py-1">Type to search…</p>}
      </div>
    </>
  );
}

// ── Shared sub-components ─────────────────────────────────────────────────────

function PanelHeader({ label }: { label: string }) {
  return (
    <div className="h-9 flex items-center px-3 shrink-0">
      <span className="text-[11px] font-semibold text-muted uppercase tracking-widest">
        {label}
      </span>
    </div>
  );
}

function SectionLabel({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <div className="flex items-center gap-1.5 px-2 py-1 mt-0.5">
      <span className="text-muted">{icon}</span>
      <span className="text-[10px] font-medium text-muted uppercase tracking-wide">{label}</span>
    </div>
  );
}

function SessionRow({ session }: { session: RecentSession }) {
  const name = session.project_path
    ? session.project_path.replace(/\\/g, "/").split("/").pop() ?? "Chat"
    : "Chat";

  const rel = React.useMemo(() => {
    const diff = Date.now() - new Date(session.updated_at).getTime();
    if (diff < 60_000)     return "now";
    if (diff < 3_600_000)  return `${Math.floor(diff / 60_000)}m ago`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
    return `${Math.floor(diff / 86_400_000)}d ago`;
  }, [session.updated_at]);

  return (
    <button className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-surface2 transition-colors duration-120 group text-left">
      <MessageSquare size={12} className="text-muted shrink-0" />
      <span className="flex-1 text-xs text-muted group-hover:text-text truncate">{name}</span>
      <span className="text-[10px] text-muted/50 shrink-0">{rel}</span>
    </button>
  );
}
