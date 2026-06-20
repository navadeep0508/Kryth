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
  { id: "chat",      label: "Go to Chat",          icon: MessageSquare, group: "action" },
  { id: "settings",  label: "Open Settings",        icon: Settings,      group: "action" },
  { id: "terminal",  label: "Toggle Terminal",      icon: Terminal,      group: "action" },
];

export default memo(function CommandPalette() {
  const { closePalette, setWorkspaceTab, toggleDrawer } = useUIStore();
  const flatNodes  = useProjectStore((s) => s.flatNodes);
  const openTab    = useEditorStore((s) => s.openTab);

  const [query, setQuery] = useState("");
  const [selectedIdx, setSelectedIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef  = useRef<HTMLUListElement>(null);

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
            setWorkspaceTab("editor");
            closePalette();
          },
        })),
    [flatNodes, openTab, setWorkspaceTab, closePalette]
  );

  const actionItems: PaletteItem[] = useMemo(
    () =>
      ACTIONS.map((a) => ({
        ...a,
        action: () => {
          if (a.id === "chat")     setWorkspaceTab("chat");
          if (a.id === "settings") setWorkspaceTab("settings");
          if (a.id === "terminal") toggleDrawer();
          closePalette();
        },
      })),
    [setWorkspaceTab, toggleDrawer, closePalette]
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
        className="absolute inset-0 bg-bg/70 backdrop-blur-sm"
        onClick={closePalette}
      />
      <div
        className="relative w-[560px] rounded-2xl border border-border bg-surface shadow-panel animate-slide-up"
        onKeyDown={onKeyDown}
      >
        {/* Search input */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
          <Search size={15} className="text-muted shrink-0" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search commands and files…"
            className="flex-1 bg-transparent text-sm text-text placeholder:text-muted outline-none"
          />
          <kbd className="text-[10px] px-1.5 py-0.5 rounded bg-surface2 border border-border font-mono text-muted">
            Esc
          </kbd>
        </div>

        {/* Results */}
        <ul ref={listRef} className="max-h-80 overflow-y-auto py-2">
          {results.length === 0 && (
            <li className="px-4 py-6 text-center text-sm text-muted">No results</li>
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
                    "w-full flex items-center gap-3 px-4 py-2 text-left transition-colors duration-120",
                    selected ? "bg-surface2" : "hover:bg-surface2/60"
                  )}
                >
                  <Icon size={14} className={selected ? "text-accent" : "text-muted"} />
                  <div className="flex-1 min-w-0">
                    <span className="text-sm text-text">{item.label}</span>
                    {item.detail && (
                      <span className="block text-xs text-muted truncate">{item.detail}</span>
                    )}
                  </div>
                  {selected && <ChevronRight size={13} className="text-muted shrink-0" />}
                </button>
              </li>
            );
          })}
        </ul>

        {/* Footer hint */}
        <div className="flex items-center justify-end gap-4 px-4 py-2 border-t border-border">
          <Hint keys={["↑", "↓"]} label="navigate" />
          <Hint keys={["↵"]} label="open" />
        </div>
      </div>
    </div>
  );
});

function Hint({ keys, label }: { keys: string[]; label: string }) {
  return (
    <span className="flex items-center gap-1 text-[10px] text-muted">
      {keys.map((k) => (
        <kbd key={k} className="px-1 py-0.5 rounded bg-surface2 border border-border font-mono">
          {k}
        </kbd>
      ))}
      <span>{label}</span>
    </span>
  );
}
