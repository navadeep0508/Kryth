import { create } from "zustand";

export interface EditorTab {
  id: string;
  path: string;
  filename: string;
  content: string;
  language: string;
  dirty: boolean;
}

export interface PendingDiff {
  id: string;
  path: string;
  before: string;
  after: string;
}

interface EditorState {
  tabs: EditorTab[];
  activeTabId: string | null;
  pendingDiff: PendingDiff | null;

  openTab: (tab: Omit<EditorTab, "id" | "dirty">) => void;
  closeTab: (id: string) => void;
  setActiveTab: (id: string) => void;
  updateContent: (id: string, content: string) => void;
  markSaved: (id: string) => void;
  setPendingDiff: (diff: PendingDiff | null) => void;
  clearPendingDiff: () => void;
}

function langFromPath(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  const MAP: Record<string, string> = {
    ts: "typescript", tsx: "typescript",
    js: "javascript", jsx: "javascript",
    py: "python", rs: "rust",
    go: "go", java: "java",
    c: "c", cpp: "cpp", h: "cpp",
    cs: "csharp", rb: "ruby",
    md: "markdown", json: "json",
    yaml: "yaml", yml: "yaml",
    toml: "toml", html: "html",
    css: "css", scss: "scss",
    sh: "shell", bash: "shell",
    sql: "sql", xml: "xml",
  };
  return MAP[ext] ?? "plaintext";
}

let _tabId = 0;

export const useEditorStore = create<EditorState>((set, get) => ({
  tabs: [],
  activeTabId: null,
  pendingDiff: null,

  openTab: (tab) => {
    const existing = get().tabs.find((t) => t.path === tab.path);
    if (existing) {
      set({ activeTabId: existing.id });
      return;
    }
    const id = `tab-${++_tabId}`;
    const newTab: EditorTab = {
      id,
      dirty: false,
      ...tab,
      language: tab.language || langFromPath(tab.path),
    };
    set((s) => ({ tabs: [...s.tabs, newTab], activeTabId: id }));
  },

  closeTab: (id) => {
    set((s) => {
      const tabs = s.tabs.filter((t) => t.id !== id);
      let activeTabId = s.activeTabId;
      if (activeTabId === id) {
        activeTabId = tabs[tabs.length - 1]?.id ?? null;
      }
      return { tabs, activeTabId };
    });
  },

  setActiveTab: (id) => set({ activeTabId: id }),

  updateContent: (id, content) => {
    set((s) => ({
      tabs: s.tabs.map((t) =>
        t.id === id ? { ...t, content, dirty: true } : t
      ),
    }));
  },

  markSaved: (id) => {
    set((s) => ({
      tabs: s.tabs.map((t) => (t.id === id ? { ...t, dirty: false } : t)),
    }));
  },

  setPendingDiff: (diff) => set({ pendingDiff: diff }),
  clearPendingDiff: () => set({ pendingDiff: null }),
}));
