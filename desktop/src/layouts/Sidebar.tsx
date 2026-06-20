import React, { memo } from "react";
import { MessageSquare, FolderOpen, Clock, Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import { useUIStore } from "@/store/uiStore";
import { useChatStore } from "@/store/chatStore";
import { request } from "@/lib/krythBridge";

interface RecentSession {
  id: string;
  project_path: string;
  updated_at: string;
  model: string;
}

export const Sidebar = memo(function Sidebar() {
  const { sidebarOpen, setWorkspaceTab } = useUIStore();
  const clearMessages = useChatStore((s) => s.clearMessages);
  const [sessions, setSessions] = React.useState<RecentSession[]>([]);

  React.useEffect(() => {
    request<{ sessions: RecentSession[] }>("GET", "/api/sessions")
      .catch(() => ({ sessions: [] }))
      .then((r) => setSessions(r.sessions?.slice(0, 12) ?? []));
  }, []);

  return (
    <aside
      className={cn(
        "flex flex-col border-r border-border bg-surface shrink-0",
        "transition-[width] duration-150 ease-out overflow-hidden",
        sidebarOpen ? "w-60" : "w-0"
      )}
    >
      <div className="flex flex-col h-full min-w-[240px]">
        {/* New chat */}
        <div className="p-3">
          <button
            onClick={() => {
              clearMessages();
              setWorkspaceTab("chat");
            }}
            className="w-full flex items-center gap-2 px-3 h-8 rounded-lg border border-border bg-surface2 hover:border-accent/40 hover:bg-surface2/80 transition-all duration-120 text-sm text-muted hover:text-text"
          >
            <Plus size={14} />
            New chat
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto px-2 pb-3 space-y-0.5">
          {/* Recent chats */}
          {sessions.length > 0 && (
            <>
              <SectionLabel icon={<Clock size={11} />} label="Recent" />
              {sessions.map((s) => (
                <SessionRow key={s.id} session={s} />
              ))}
            </>
          )}
        </nav>

        {/* Bottom nav */}
        <div className="border-t border-border p-2 space-y-0.5">
          <NavItem
            icon={<MessageSquare size={14} />}
            label="Chat"
            onClick={() => setWorkspaceTab("chat")}
          />
          <NavItem
            icon={<FolderOpen size={14} />}
            label="Explorer"
            onClick={() => setWorkspaceTab("editor")}
          />
        </div>
      </div>
    </aside>
  );
});

function SectionLabel({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <div className="flex items-center gap-1.5 px-2 py-1.5 mt-1">
      <span className="text-muted">{icon}</span>
      <span className="text-[11px] font-medium text-muted uppercase tracking-wide">
        {label}
      </span>
    </div>
  );
}

function SessionRow({ session }: { session: RecentSession }) {
  const name = session.project_path
    ? session.project_path.replace(/\\/g, "/").split("/").pop() ?? "Chat"
    : "Chat";

  const rel = React.useMemo(() => {
    const d = new Date(session.updated_at);
    const diff = Date.now() - d.getTime();
    if (diff < 60_000) return "now";
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
    return `${Math.floor(diff / 86_400_000)}d ago`;
  }, [session.updated_at]);

  return (
    <button className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-surface2 transition-colors duration-120 group text-left">
      <MessageSquare size={13} className="text-muted shrink-0" />
      <span className="flex-1 text-sm text-muted group-hover:text-text truncate">
        {name}
      </span>
      <span className="text-[10px] text-muted/60 shrink-0">{rel}</span>
    </button>
  );
}

function NavItem({
  icon, label, onClick,
}: {
  icon: React.ReactNode; label: string; onClick?: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-muted hover:text-text hover:bg-surface2 transition-colors duration-120 text-sm"
    >
      {icon}
      {label}
    </button>
  );
}
