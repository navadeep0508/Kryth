import { memo } from "react";
import { X, Circle } from "lucide-react";
import { cn } from "@/lib/utils";
import { useEditorStore } from "@/store/editorStore";

export const TabBar = memo(function TabBar() {
  const { tabs, activeTabId, setActiveTab, closeTab } = useEditorStore();

  if (tabs.length === 0) return null;

  return (
    <div className="flex items-stretch h-9 border-b border-border-soft bg-panel overflow-x-auto scrollbar-none shrink-0">
      {tabs.map((tab) => {
        const active = tab.id === activeTabId;
        const name = tab.path.replace(/\\/g, "/").split("/").pop() ?? tab.path;
        return (
          <div
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={cn(
              "relative flex items-center gap-1.5 px-3 text-xs cursor-pointer shrink-0",
              "border-r border-border-soft transition-colors duration-100",
              active
                ? "bg-bg text-text"
                : "bg-panel text-muted hover:text-text hover:bg-panel-hover"
            )}
          >
            {active && (
              <span className="absolute bottom-0 left-2 right-2 h-px bg-accent" />
            )}
            {tab.dirty && (
              <Circle size={6} className="fill-accent text-accent shrink-0" />
            )}
            <span className="max-w-[120px] truncate">{name}</span>
            <button
              onClick={(e) => { e.stopPropagation(); closeTab(tab.id); }}
              className="ml-0.5 p-0.5 rounded hover:bg-panel-hover text-dim hover:text-muted transition-colors duration-100"
            >
              <X size={11} />
            </button>
          </div>
        );
      })}
    </div>
  );
});
