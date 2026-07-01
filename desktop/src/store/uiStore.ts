import { create } from "zustand";

export type AgentStatus =
  | "idle"
  | "thinking"
  | "planning"
  | "executing"
  | "editing"
  | "waiting_approval"
  | "done"
  | "error";

export type SideTab = "chats" | "files" | "tools" | "memory" | "agents" | "browser" | "settings";
export type InspectorTab = "context" | "diff" | "logs" | "agents" | "approvals";
export type DockTab = "terminal" | "logs" | "debug";
export type ConnStatus = "connecting" | "connected" | "disconnected";
export type WorkspaceView = "workspace" | "editor" | "split";
export type CenterView = "chat" | "settings";

interface UIState {
  sideTab: SideTab;
  centerView: CenterView;
  sideWidth: number;
  sideCollapsed: boolean;

  browserActive: boolean;  // true when browser automation is running

  inspectorTab: InspectorTab;
  inspectorWidth: number;
  inspectorOpen: boolean;

  dockTab: DockTab;
  dockHeight: number;
  dockOpen: boolean;

  rightWidth: number;
  rightCollapsed: boolean;

  workspaceView: WorkspaceView;

  connStatus: ConnStatus;
  agentStatus: AgentStatus;
  paletteOpen: boolean;

  currentModel: string;
  execMode: "auto" | "fast" | "deep" | "max";

  tokenBudget: { used: number; limit: number; remaining: number } | null;
  sessionTokens: { prompt: number; completion: number; total: number };

  setSideTab: (t: SideTab) => void;
  setCenterView: (v: CenterView) => void;
  setSideWidth: (w: number) => void;
  toggleSidebar: () => void;

  setInspectorTab: (t: InspectorTab) => void;
  setInspectorWidth: (w: number) => void;
  toggleInspector: () => void;

  setDockTab: (t: DockTab) => void;
  setDockHeight: (h: number) => void;
  toggleDock: () => void;
  openDock: (tab?: DockTab) => void;

  setConnStatus: (s: ConnStatus) => void;
  setAgentStatus: (s: AgentStatus) => void;
  setCurrentModel: (m: string) => void;
  setExecMode: (m: "auto" | "fast" | "deep" | "max") => void;
  setRightWidth: (w: number) => void;
  toggleRightPanel: () => void;

  setWorkspaceView: (v: WorkspaceView) => void;

  openPalette: () => void;
  closePalette: () => void;

  setTokenBudget: (budget: { used: number; limit: number; remaining: number }) => void;
  addSessionTokens: (usage: { prompt: number; completion: number; total: number }) => void;
  setBrowserActive: (active: boolean) => void;
}

export const useUIStore = create<UIState>((set) => ({
  sideTab: "chats",
  centerView: "chat" as CenterView,
  sideWidth: 300,
  sideCollapsed: false,

  browserActive: false,

  inspectorTab: "context",
  inspectorWidth: 320,
  inspectorOpen: false,

  dockTab: "terminal",
  dockHeight: 200,
  dockOpen: false,

  connStatus: "connecting",
  agentStatus: "idle",
  paletteOpen: false,
  rightWidth: 260,
  rightCollapsed: false,

  workspaceView: "workspace",

  currentModel: "step-3.5-flash",
  execMode: "auto",

  tokenBudget: null,
  sessionTokens: { prompt: 0, completion: 0, total: 0 },

  setSideTab: (t) => set({ sideTab: t, sideCollapsed: false }),
  setCenterView: (v) => set({ centerView: v }),
  setSideWidth: (w) => set({ sideWidth: Math.max(200, Math.min(480, w)) }),
  toggleSidebar: () => set((s) => ({ sideCollapsed: !s.sideCollapsed })),

  setInspectorTab: (t) => set({ inspectorTab: t, inspectorOpen: true }),
  setInspectorWidth: (w) => set({ inspectorWidth: Math.max(240, Math.min(500, w)) }),
  toggleInspector: () => set((s) => ({ inspectorOpen: !s.inspectorOpen })),

  setRightWidth: (w) => set({ rightWidth: Math.max(200, Math.min(400, w)) }),
  toggleRightPanel: () => set((s) => ({ rightCollapsed: !s.rightCollapsed })),

  setDockTab: (t) => set({ dockTab: t, dockOpen: true }),
  setDockHeight: (h) => set({ dockHeight: Math.max(100, Math.min(500, h)) }),
  toggleDock: () => set((s) => ({ dockOpen: !s.dockOpen })),
  openDock: (tab) => set((s) => ({ dockOpen: true, dockTab: tab ?? s.dockTab })),

  setConnStatus: (s) => set({ connStatus: s }),
  setAgentStatus: (s) => set({ agentStatus: s }),
  setCurrentModel: (m) => set({ currentModel: m }),
  setExecMode: (m) => set({ execMode: m }),
  setWorkspaceView: (v) => set({ workspaceView: v }),

  openPalette: () => set({ paletteOpen: true }),
  closePalette: () => set({ paletteOpen: false }),

  setTokenBudget: (budget) => set({ tokenBudget: budget }),
  addSessionTokens: (usage) => set((s) => ({
    sessionTokens: {
      prompt: s.sessionTokens.prompt + usage.prompt,
      completion: s.sessionTokens.completion + usage.completion,
      total: s.sessionTokens.total + usage.total,
    },
  })),
  setBrowserActive: (active) => set({ browserActive: active }),
}));
