import { memo, useCallback, useState } from "react";
import { cn } from "@/lib/utils";
import { useUIStore } from "@/store/uiStore";

interface Props {
  sidebar: React.ReactNode;
  center: React.ReactNode;
  inspector: React.ReactNode;
  commandBar: React.ReactNode;
  bottomDock?: React.ReactNode;
  topBar?: React.ReactNode;
}

export const AppShell = memo(function AppShell({ sidebar, center, inspector, commandBar, bottomDock, topBar }: Props) {
  const { sideWidth, rightWidth, sideCollapsed, rightCollapsed } = useUIStore();

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-bg text-text font-mono">
      {/* Top bar */}
      {topBar && <div className="shrink-0">{topBar}</div>}

      {/* Main body */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left Sidebar (collapsible) */}
        {!sideCollapsed && <SidebarColumn width={sideWidth}>{sidebar}</SidebarColumn>}

        {/* Center: workspace + bottom dock + command bar */}
        <div className="flex flex-col flex-1 min-w-0 border-l border-border">
          <div className="flex-1 overflow-hidden">{center}</div>
          {bottomDock}
          <div className="shrink-0">{commandBar}</div>
        </div>

        {/* Right Inspector (collapsible) */}
        {!rightCollapsed && <InspectorColumn width={rightWidth}>{inspector}</InspectorColumn>}
      </div>
    </div>
  );
});

/* ── Sidebar column with resize handle ─────────────────────── */
function SidebarColumn({ width, children }: { width: number; children: React.ReactNode }) {
  const setSideWidth = useUIStore((s) => s.setSideWidth);
  const [resizing, setResizing] = useState(false);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      setResizing(true);
      const startX = e.clientX;
      const startWidth = width;

      const onMove = (ev: MouseEvent) => {
        const delta = ev.clientX - startX;
        setSideWidth(startWidth + delta);
      };
      const onUp = () => {
        setResizing(false);
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [width, setSideWidth]
  );

  return (
    <div
      className={cn("shrink-0 flex overflow-hidden", resizing && "pointer-events-none")}
      style={{ width }}
    >
      <div className="flex-1 overflow-hidden">{children}</div>
      <div
        onMouseDown={handleMouseDown}
        className={cn(
          "w-[3px] shrink-0 cursor-col-resize hover:bg-accent/30 active:bg-accent/60 transition-colors duration-100",
          resizing && "bg-accent/60"
        )}
      />
    </div>
  );
}

/* ── Inspector column with resize handle ───────────────────── */
function InspectorColumn({ width, children }: { width: number; children: React.ReactNode }) {
  const setRightWidth = useUIStore((s) => s.setRightWidth);
  const [resizing, setResizing] = useState(false);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      setResizing(true);
      const startX = e.clientX;
      const startWidth = width;

      const onMove = (ev: MouseEvent) => {
        const delta = startX - ev.clientX;
        setRightWidth(startWidth + delta);
      };
      const onUp = () => {
        setResizing(false);
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [width, setRightWidth]
  );

  return (
    <div
      className={cn("shrink-0 flex overflow-hidden border-l border-border", resizing && "pointer-events-none")}
      style={{ width }}
    >
      <div
        onMouseDown={handleMouseDown}
        className={cn(
          "w-[3px] shrink-0 cursor-col-resize hover:bg-accent/30 active:bg-accent/60 transition-colors duration-100",
          resizing && "bg-accent/60"
        )}
      />
      <div className="flex-1 overflow-hidden">{children}</div>
    </div>
  );
}
