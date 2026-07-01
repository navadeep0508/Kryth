import React, { memo, useState, useMemo, useCallback, useRef } from "react";
import Fuse from "fuse.js";
import { Search, X } from "lucide-react";
import { useEditorStore } from "@/store/editorStore";
import { useProjectStore } from "@/store/projectStore";
import { bridge } from "@/lib/krythBridge";

interface FileEntry {
  path: string;
  name: string;
}

function flattenTree(nodes: ReturnType<typeof useProjectStore.getState>["flatNodes"]): FileEntry[] {
  return nodes
    .filter((n) => !n.is_dir)
    .map((n) => ({ path: n.path, name: n.name }));
}

export const FileSearch = memo(function FileSearch() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const flatNodes = useProjectStore((s) => s.flatNodes);
  const openTab = useEditorStore((s) => s.openTab);

  const files = useMemo(() => flattenTree(flatNodes), [flatNodes]);

  const fuse = useMemo(
    () =>
      new Fuse(files, {
        keys: ["name", "path"],
        threshold: 0.4,
        includeScore: true,
      }),
    [files]
  );

  const results = useMemo(
    () => (query.trim() ? fuse.search(query).slice(0, 10).map((r) => r.item) : files.slice(0, 10)),
    [query, fuse, files]
  );

  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "p") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  React.useEffect(() => {
    if (open) {
      setQuery("");
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  const openFile = useCallback(
    async (file: FileEntry) => {
      setOpen(false);
      try {
        const content = await bridge.readFile(file.path);
        openTab({ path: file.path, filename: file.name, content, language: "" });
      } catch {
        openTab({ path: file.path, filename: file.name, content: "", language: "" });
      }
    },
    [openTab]
  );

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-24"
      onClick={() => setOpen(false)}
    >
      <div
        className="w-[520px] rounded-xl border border-border bg-panel shadow-modal animate-slide-down"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 px-3 py-2.5 border-b border-border-soft">
          <Search size={14} className="text-dim shrink-0" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Go to file..."
            className="flex-1 bg-transparent text-sm text-text placeholder:text-dim outline-none"
          />
          <button onClick={() => setOpen(false)} className="text-dim hover:text-muted transition-colors duration-100">
            <X size={13} />
          </button>
        </div>
        <ul className="max-h-72 overflow-y-auto py-1">
          {results.map((f) => (
            <li key={f.path}>
              <button
                onClick={() => openFile(f)}
                className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-panel-hover transition-colors duration-100"
              >
                <span className="text-sm text-text">{f.name}</span>
                <span className="text-xs text-dim truncate">{f.path}</span>
              </button>
            </li>
          ))}
          {results.length === 0 && (
            <li className="px-3 py-4 text-sm text-dim text-center">No files found</li>
          )}
        </ul>
      </div>
    </div>
  );
});
