import React, { memo, useState, useMemo, useRef, useEffect, useCallback } from "react";
import Fuse from "fuse.js";
import { Search, FileText, Settings, MessageSquare, Terminal, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { useUIStore } from "@/store/uiStore";
import { useProjectStore } from "@/store/projectStore";
import { useEditorStore } from "@/store/editorStore";
import { bridge } from "@/lib/krythBridge";

interface PaletteItem {
  id: string;
  label: string;
  detail?: string;
  icon: React.ElementType;
  group: "action" | "file";
  action: () => void;
}

const ACTIONS: Omit<PaletteItem, "action">[] = [
  { id: "chat",      label: "Go to Chat",     icon: MessageSquare, group: "action" },
  { id: "settings",  label: "Open Settings",   icon: Settings,      group: "action" },
  { id: "terminal",  label: "Toggle Terminal", icon: Terminal,      group: "action" },
];

export default memo(function CommandPalette() {
  const closePalette = useUIStore((s) => s.closePalette);
  const setSideTab = useUIStore((s) => s.setSideTab);
  const toggleDock = useUIStore((s) => s.toggleDock);
  const flatNodes = useProjectStore((s) => s.flatNodes);
  const openTab = useEditorStore((s) => s.openTab);

  const [query, setQuery] = useState("");
  const [selectedIdx, setSelectedIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const files: PaletteItem[] = useMemo(
    () =>
      flatNodes
        .filter((n) => !n.is_dir)
        .slice(0, 200)
        .map((n) => ({
          id: `file:${n.path}`,
          label: n.name,
          detail: n.path,
          icon: FileText,
          group: "file" as const,
          action: async () => {
            const content = await bridge.readFile(n.path).catch(() => "");
            openTab({ path: n.path, filename: n.name, content, language: "" });
            closePalette();
          },
        })),
    [flatNodes, openTab, closePalette]
  );

  const actionItems: PaletteItem[] = useMemo(
    () =>
      ACTIONS.map((a) => ({
        ...a,
        action: () => {
          if (a.id === "chat") setSideTab("chats");
          if (a.id === "settings") useUIStore.getState().setCenterView("settings");
          if (a.id === "terminal") toggleDock();
          closePalette();
        },
      })),
    [setSideTab, toggleDock, closePalette]
  );

  const allItems = useMemo(() => [...actionItems, ...files], [actionItems, files]);

  const fuse = useMemo(
    () =>
      new Fuse(allItems, {
        keys: ["label", "detail"],
        threshold: 0.4,
        includeScore: true,
      }),
    [allItems]
  );

  const results = useMemo(
    () => (query.trim() ? fuse.search(query).slice(0, 15).map((r) => r.item) : allItems.slice(0, 15)),
    [query, fuse, allItems]
  );

  useEffect(() => setSelectedIdx(0), [query]);

  const selectAndRun = useCallback(
    (item: PaletteItem) => item.action(),
    []
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIdx((i) => Math.min(i + 1, results.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIdx((i) => Math.max(i - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (results[selectedIdx]) selectAndRun(results[selectedIdx]);
      } else if (e.key === "Escape") {
        closePalette();
      }
    },
    [results, selectedIdx, selectAndRun, closePalette]
  );

  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center pt-20">
      <div
        className="absolute inset-0 bg-bg/80 backdrop-blur-sm"
        onClick={closePalette}
      />
      <div
        className="relative w-[560px] rounded-xl border border-border bg-panel shadow-modal animate-slide-down"
        onKeyDown={onKeyDown}
      >
        {/* Search input */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border-soft">
          <Search size={15} className="text-dim shrink-0" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search commands and files..."
            className="flex-1 bg-transparent text-sm text-text placeholder:text-dim outline-none"
          />
          <kbd className="text-2xs px-1.5 py-0.5 rounded bg-sidebar border border-border font-mono text-dim">
            Esc
          </kbd>
        </div>

        {/* Results */}
        <ul ref={listRef} className="max-h-80 overflow-y-auto py-1">
          {results.length === 0 && (
            <li className="px-4 py-6 text-center text-sm text-dim">No results</li>
          )}
          {results.map((item, i) => {
            const Icon = item.icon;
            const selected = i === selectedIdx;
            return (
              <li key={item.id}>
                <button
                  onClick={() => selectAndRun(item)}
                  onMouseEnter={() => setSelectedIdx(i)}
                  className={cn(
                    "w-full flex items-center gap-3 px-4 py-2 text-left transition-colors duration-100",
                    selected ? "bg-panel-hover" : "hover:bg-panel-hover/50"
                  )}
                >
                  <Icon size={14} className={selected ? "text-accent" : "text-dim"} />
                  <div className="flex-1 min-w-0">
                    <span className="text-sm text-text">{item.label}</span>
                    {item.detail && (
                      <span className="block text-xs text-dim truncate">{item.detail}</span>
                    )}
                  </div>
                  {selected && <ChevronRight size={12} className="text-dim shrink-0" />}
                </button>
              </li>
            );
          })}
        </ul>

        {/* Footer */}
        <div className="flex items-center justify-end gap-4 px-4 py-2 border-t border-border-soft">
          <Hint keys={["↑", "↓"]} label="navigate" />
          <Hint keys={["↵"]} label="open" />
        </div>
      </div>
    </div>
  );
});

function Hint({ keys, label }: { keys: string[]; label: string }) {
  return (
    <span className="flex items-center gap-1 text-2xs text-dim">
      {keys.map((k) => (
        <kbd key={k} className="px-1 py-0.5 rounded bg-sidebar border border-border font-mono">
          {k}
        </kbd>
      ))}
      <span>{label}</span>
    </span>
  );
}
