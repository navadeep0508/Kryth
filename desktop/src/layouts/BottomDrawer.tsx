import React, { memo, lazy, Suspense } from "react";
import { Terminal, FileText, ChevronUp, ChevronDown, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useUIStore } from "@/store/uiStore";

const TerminalPanel = lazy(() => import("@/features/terminal/TerminalPanel"));

export const BottomDrawer = memo(function BottomDrawer() {
  const { drawerOpen, drawerTab, toggleDrawer, setDrawerTab } = useUIStore();

  return (
    <div
      className={cn(
        "border-t border-[rgba(255,255,255,0.05)] bg-surface shrink-0",
        "transition-[height] duration-150 ease-out overflow-hidden",
        drawerOpen ? "h-60" : "h-8"
      )}
    >
      {/* Handle strip */}
      <div className="h-8 flex items-center px-2 gap-0.5 shrink-0">
        <DrawerTab label="Terminal" icon={<Terminal size={11} />} active={drawerTab === "terminal"}
          onClick={() => drawerTab === "terminal" ? toggleDrawer() : setDrawerTab("terminal")} />
        <DrawerTab label="Logs" icon={<FileText size={11} />} active={drawerTab === "logs"}
          onClick={() => drawerTab === "logs" ? toggleDrawer() : setDrawerTab("logs")} />

        <div className="flex-1" />

        <button
          onClick={toggleDrawer}
          className="p-1 rounded text-subtle hover:text-muted transition-colors duration-100"
          title={drawerOpen ? "Collapse" : "Expand"}
        >
          {drawerOpen ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
        </button>

        {drawerOpen && (
          <button onClick={toggleDrawer} className="p-1 rounded text-subtle hover:text-muted transition-colors duration-100" title="Close">
            <X size={12} />
          </button>
        )}
      </div>

      {drawerOpen && (
        <div className="h-[calc(100%-32px)] overflow-hidden bg-[#0A0A0A]">
          {drawerTab === "terminal" && (
            <DrawerErrorBoundary label="terminal">
              <Suspense fallback={<Loading />}>
                <TerminalPanel />
              </Suspense>
            </DrawerErrorBoundary>
          )}
          {drawerTab === "logs" && <LogsPane />}
        </div>
      )}
    </div>
  );
});

function DrawerTab({ label, icon, active, onClick }: { label: string; icon: React.ReactNode; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 px-2 h-6 rounded text-xs transition-colors duration-100",
        active ? "bg-[rgba(255,255,255,0.06)] text-text" : "text-subtle hover:text-muted"
      )}
    >
      {icon}
      {label}
    </button>
  );
}

function Loading() {
  return <div className="flex items-center justify-center h-full text-subtle text-xs">Loading…</div>;
}

function LogsPane() {
  return <div className="h-full overflow-auto p-3 font-mono text-xs text-subtle"><p>No logs.</p></div>;
}

class DrawerErrorBoundary extends React.Component<
  { children: React.ReactNode; label: string },
  { hasError: boolean }
> {
  state = { hasError: false };
  static getDerivedStateFromError() { return { hasError: true }; }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex flex-col items-center justify-center h-full text-subtle text-xs gap-2">
          <span>{this.props.label} crashed</span>
          <button onClick={() => this.setState({ hasError: false })} className="text-text underline">Reload</button>
        </div>
      );
    }
    return this.props.children;
  }
}
