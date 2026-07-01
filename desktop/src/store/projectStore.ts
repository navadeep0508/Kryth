import { create } from "zustand";
import { bridge, type FileEntry } from "@/lib/krythBridge";

export interface TreeNode {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  depth: number;
  expanded: boolean;
  children?: TreeNode[];
  loaded: boolean;
}

const STORAGE_KEY = "kryth:lastCwd";

interface ProjectState {
  cwd: string;
  roots: TreeNode[];
  flatNodes: TreeNode[];   // flattened visible tree for virtualization
  searchQuery: string;
  searchOpen: boolean;
  fileIndex: string[];     // flat list of all known paths for search

  setCwd: (cwd: string) => void;
  setRoots: (entries: FileEntry[]) => void;
  toggleExpand: (path: string, children?: FileEntry[]) => void;
  setSearchQuery: (q: string) => void;
  setSearchOpen: (open: boolean) => void;
  addToIndex: (paths: string[]) => void;
  openFolder: (path: string) => Promise<void>;
  restoreLastFolder: () => Promise<void>;
}

function entriesToNodes(entries: FileEntry[], depth: number): TreeNode[] {
  return entries.map((e) => ({
    name: e.name,
    path: e.path,
    is_dir: e.is_dir,
    size: e.size,
    depth,
    expanded: false,
    loaded: false,
    children: e.is_dir ? [] : undefined,
  }));
}

function flatten(nodes: TreeNode[]): TreeNode[] {
  const result: TreeNode[] = [];
  for (const n of nodes) {
    result.push(n);
    if (n.is_dir && n.expanded && n.children) {
      result.push(...flatten(n.children));
    }
  }
  return result;
}

function updateNode(
  nodes: TreeNode[],
  path: string,
  fn: (n: TreeNode) => TreeNode
): TreeNode[] {
  return nodes.map((n) => {
    if (n.path === path) return fn(n);
    if (n.children) return { ...n, children: updateNode(n.children, path, fn) };
    return n;
  });
}

export const useProjectStore = create<ProjectState>((set, get) => ({
  cwd: "",
  roots: [],
  flatNodes: [],
  searchQuery: "",
  searchOpen: false,
  fileIndex: [],

  setCwd: (cwd) => {
    set({ cwd });
    try { localStorage.setItem(STORAGE_KEY, cwd); } catch { /* noop */ }
  },

  setRoots: (entries) => {
    const roots = entriesToNodes(entries, 0);
    set({
      roots,
      flatNodes: flatten(roots),
      fileIndex: entries.filter((e) => !e.is_dir).map((e) => e.path),
    });
  },

  toggleExpand: (path, children) => {
    const roots = updateNode(get().roots, path, (n) => {
      const expanded = !n.expanded;
      const newChildren = children
        ? entriesToNodes(children, n.depth + 1)
        : n.children ?? [];
      return { ...n, expanded, children: newChildren, loaded: children != null };
    });
    set({ roots, flatNodes: flatten(roots) });
  },

  setSearchQuery: (q) => set({ searchQuery: q }),
  setSearchOpen: (open) => set({ searchOpen: open }),
  addToIndex: (paths) =>
    set((s) => ({ fileIndex: [...new Set([...s.fileIndex, ...paths])] })),

  openFolder: async (path: string) => {
    const entries = await bridge.listFiles(path);
    get().setRoots(entries);
    get().setCwd(path);
    // Sync to workspace store so agent runs use this folder
    const { useWorkspaceStore } = await import("@/store/workspaceStore");
    useWorkspaceStore.getState().setCwd(path);
  },

  restoreLastFolder: async () => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        await get().openFolder(saved);
        // Sync workspace CWD so agent runs use the restored folder
        const { useWorkspaceStore } = await import("@/store/workspaceStore");
        useWorkspaceStore.getState().setCwd(saved);
      }
    } catch {
      // Storage unavailable or folder no longer exists
    }
  },
}));
