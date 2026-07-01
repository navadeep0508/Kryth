import React, { memo, useCallback, useState, useRef } from "react";
import { FixedSizeList as List } from "react-window";
import {
  ChevronRight, ChevronDown, File, Folder, FolderOpen,
  FileCode, FileText, FileJson, Image, Settings, Database,
  Terminal as TermIcon, Package, Coffee, Braces, Hash,
  MoreHorizontal, FilePlus, FolderPlus, Trash2, Copy, Pencil,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useProjectStore, type TreeNode } from "@/store/projectStore";
import { useEditorStore } from "@/store/editorStore";
import { bridge } from "@/lib/krythBridge";

const ROW_HEIGHT = 28;

// File icon mapping by extension
const EXT_ICONS: Record<string, { icon: React.ElementType; color: string }> = {
  ts: { icon: Braces, color: "text-blue-500" },
  tsx: { icon: Braces, color: "text-blue-500" },
  js: { icon: Coffee, color: "text-yellow-500" },
  jsx: { icon: Coffee, color: "text-yellow-500" },
  py: { icon: Hash, color: "text-green-500" },
  rs: { icon: Settings, color: "text-orange-500" },
  json: { icon: FileJson, color: "text-yellow-600" },
  toml: { icon: FileJson, color: "text-orange-400" },
  yaml: { icon: FileJson, color: "text-red-400" },
  yml: { icon: FileJson, color: "text-red-400" },
  md: { icon: FileText, color: "text-blue-400" },
  txt: { icon: FileText, color: "text-gray-400" },
  html: { icon: FileCode, color: "text-orange-500" },
  css: { icon: FileCode, color: "text-blue-400" },
  scss: { icon: FileCode, color: "text-pink-400" },
  svg: { icon: Image, color: "text-green-400" },
  png: { icon: Image, color: "text-purple-400" },
  jpg: { icon: Image, color: "text-purple-400" },
  ico: { icon: Image, color: "text-purple-400" },
  sql: { icon: Database, color: "text-blue-600" },
  sh: { icon: TermIcon, color: "text-green-500" },
  bat: { icon: TermIcon, color: "text-green-500" },
  lock: { icon: Package, color: "text-gray-400" },
  gitignore: { icon: Settings, color: "text-gray-500" },
  env: { icon: Settings, color: "text-yellow-600" },
};

function getFileIcon(name: string): { icon: React.ElementType; color: string } {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  // Special filenames
  if (name === "package.json") return { icon: Package, color: "text-green-500" };
  if (name === "Cargo.toml") return { icon: Package, color: "text-orange-500" };
  if (name === "Dockerfile") return { icon: Database, color: "text-blue-500" };
  if (name.startsWith(".")) return EXT_ICONS[name.slice(1)] ?? { icon: Settings, color: "text-gray-500" };
  return EXT_ICONS[ext] ?? { icon: File, color: "text-gray-400" };
}

function formatSize(bytes: number): string {
  if (bytes === 0) return "";
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)}K`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}M`;
}

export const FileTree = memo(function FileTree({ height }: { height: number }) {
  const { flatNodes, toggleExpand } = useProjectStore();
  const openTab = useEditorStore((s) => s.openTab);
  const activeTabPath = useEditorStore((s) => s.tabs.find((t) => t.id === s.activeTabId)?.path ?? "");
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; node: TreeNode } | null>(null);

  const handleClick = useCallback(async (node: TreeNode) => {
    if (node.is_dir) {
      if (!node.loaded) {
        try {
          const children = await bridge.listFiles(node.path);
          toggleExpand(node.path, children);
        } catch {
          toggleExpand(node.path);
        }
      } else {
        toggleExpand(node.path);
      }
    } else {
      try {
        const content = await bridge.readFile(node.path);
        openTab({ path: node.path, filename: node.name, content, language: "" });
      } catch {
        openTab({ path: node.path, filename: node.name, content: "", language: "" });
      }
    }
  }, [toggleExpand, openTab]);

  const handleContextMenu = useCallback((e: React.MouseEvent, node: TreeNode) => {
    e.preventDefault();
    setContextMenu({ x: e.clientX, y: e.clientY, node });
  }, []);

  const closeContextMenu = useCallback(() => setContextMenu(null), []);

  return (
    <>
      <List
        height={height}
        width="100%"
        itemCount={flatNodes.length}
        itemSize={ROW_HEIGHT}
      >
        {({ index, style }) => (
          <FileRow
            key={flatNodes[index].path}
            node={flatNodes[index]}
            style={style}
            onClick={handleClick}
            onContextMenu={handleContextMenu}
            isActive={flatNodes[index].path === activeTabPath}
          />
        )}
      </List>
      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          node={contextMenu.node}
          onClose={closeContextMenu}
        />
      )}
    </>
  );
});

const FileRow = memo(function FileRow({
  node, style, onClick, onContextMenu, isActive,
}: {
  node: TreeNode;
  style: React.CSSProperties;
  onClick: (node: TreeNode) => void;
  onContextMenu: (e: React.MouseEvent, node: TreeNode) => void;
  isActive: boolean;
}) {
  const { icon: FileIcon, color } = node.is_dir
    ? { icon: node.expanded ? FolderOpen : Folder, color: "text-accent/70" }
    : getFileIcon(node.name);

  return (
    <div
      style={style}
      onClick={() => onClick(node)}
      onContextMenu={(e) => onContextMenu(e, node)}
      className={cn(
        "flex items-center gap-1.5 px-2 cursor-pointer transition-colors duration-75 group",
        isActive ? "bg-accent/10 text-text" : "hover:bg-panel-hover",
      )}
    >
      {/* Indent with visual guides */}
      <span className="shrink-0 flex items-center" style={{ width: node.depth * 14 }}>
        {Array.from({ length: node.depth }).map((_, i) => (
          <span key={i} className="w-[14px] h-full flex items-center justify-center">
            <span className="w-px h-full bg-border-soft/50" />
          </span>
        ))}
      </span>

      {/* Expand arrow */}
      {node.is_dir ? (
        <span className="w-4 h-4 flex items-center justify-center shrink-0 text-dim">
          {node.expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </span>
      ) : (
        <span className="w-4 shrink-0" />
      )}

      {/* Icon */}
      <FileIcon size={14} className={cn("shrink-0", color)} />

      {/* Name */}
      <span className={cn(
        "text-[12px] truncate flex-1",
        node.is_dir ? "font-medium text-text" : "text-muted group-hover:text-text",
        isActive && "text-text font-medium",
      )}>
        {node.name}
      </span>

      {/* File size (for files only) */}
      {!node.is_dir && node.size > 0 && (
        <span className="text-[9px] text-faint font-mono shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
          {formatSize(node.size)}
        </span>
      )}
    </div>
  );
});

/* ═══ Context Menu ═══ */
function ContextMenu({ x, y, node, onClose }: { x: number; y: number; node: TreeNode; onClose: () => void }) {
  const menuRef = useRef<HTMLDivElement>(null);
  const openTab = useEditorStore((s) => s.openTab);

  // Close on click outside
  React.useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  // Close on escape
  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  const handleNewFile = async () => {
    onClose();
    const name = prompt("New file name:");
    if (!name) return;
    const dir = node.is_dir ? node.path : node.path.replace(/[\\/][^\\/]+$/, "");
    const sep = dir.includes("/") ? "/" : "\\";
    const newPath = `${dir}${sep}${name}`;
    try {
      await bridge.writeFile(newPath, "");
      openTab({ path: newPath, filename: name, content: "", language: "" });
      // Refresh parent
      const { openFolder } = useProjectStore.getState();
      await openFolder(useProjectStore.getState().cwd);
    } catch (e) {
      alert(`Failed to create file: ${e}`);
    }
  };

  const handleNewFolder = async () => {
    onClose();
    const name = prompt("New folder name:");
    if (!name) return;
    const dir = node.is_dir ? node.path : node.path.replace(/[\\/][^\\/]+$/, "");
    const sep = dir.includes("/") ? "/" : "\\";
    const newPath = `${dir}${sep}${name}${sep}.gitkeep`;
    try {
      await bridge.writeFile(newPath, "");
      const { openFolder } = useProjectStore.getState();
      await openFolder(useProjectStore.getState().cwd);
    } catch (e) {
      alert(`Failed to create folder: ${e}`);
    }
  };

  const handleDelete = async () => {
    onClose();
    if (!confirm(`Delete "${node.name}"?`)) return;
    try {
      await bridge.runShell(`del "${node.path}"`, "");
      const { openFolder } = useProjectStore.getState();
      await openFolder(useProjectStore.getState().cwd);
    } catch (e) {
      alert(`Failed to delete: ${e}`);
    }
  };

  const handleCopyPath = () => {
    onClose();
    navigator.clipboard.writeText(node.path);
  };

  const handleRename = async () => {
    onClose();
    const newName = prompt("Rename to:", node.name);
    if (!newName || newName === node.name) return;
    const dir = node.path.replace(/[\\/][^\\/]+$/, "");
    const sep = dir.includes("/") ? "/" : "\\";
    const newPath = `${dir}${sep}${newName}`;
    try {
      await bridge.runShell(`move "${node.path}" "${newPath}"`, "");
      const { openFolder } = useProjectStore.getState();
      await openFolder(useProjectStore.getState().cwd);
    } catch (e) {
      alert(`Failed to rename: ${e}`);
    }
  };

  const items = [
    ...(node.is_dir ? [
      { label: "New File", icon: FilePlus, action: handleNewFile },
      { label: "New Folder", icon: FolderPlus, action: handleNewFolder },
      { label: "---", icon: null, action: () => {} },
    ] : []),
    { label: "Rename", icon: Pencil, action: handleRename },
    { label: "Copy Path", icon: Copy, action: handleCopyPath },
    { label: "---", icon: null, action: () => {} },
    { label: "Delete", icon: Trash2, action: handleDelete, danger: true },
  ];

  return (
    <div
      ref={menuRef}
      className="fixed z-50 bg-panel border border-border rounded-lg shadow-lg py-1 min-w-[160px] animate-fade-in"
      style={{ left: x, top: y }}
    >
      {items.map((item, i) =>
        item.label === "---" ? (
          <div key={i} className="h-px bg-border-soft mx-2 my-1" />
        ) : (
          <button
            key={i}
            onClick={item.action}
            className={cn(
              "w-full flex items-center gap-2 px-3 py-1.5 text-xs text-left transition-colors",
              (item as any).danger
                ? "text-danger hover:bg-danger/10"
                : "text-muted hover:bg-panel-hover hover:text-text"
            )}
          >
            {item.icon && <item.icon size={12} />}
            {item.label}
          </button>
        )
      )}
    </div>
  );
}
