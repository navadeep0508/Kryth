import { create } from "zustand";

export type WorkspaceTab  = "chat" | "editor" | "settings";
export type DrawerTab     = "terminal" | "logs";
export type ConnStatus    = "connecting" | "connected" | "disconnected";
export type SideActivity  = "chat" | "explorer" | "search";

interface UIState {
  sideActivity:  SideActivity | null;  // null = panel collapsed
  inspectorOpen: boolean;
  drawerOpen:    boolean;
  drawerTab:     DrawerTab;
  workspaceTab:  WorkspaceTab;
  connStatus:    ConnStatus;
  paletteOpen:   boolean;

  setSideActivity:  (a: SideActivity | null) => void;
  toggleSideActivity: (a: SideActivity) => void;
  toggleSidebar:    () => void;
  toggleInspector:  () => void;
  toggleDrawer:     () => void;
  openDrawer:       (tab?: DrawerTab) => void;
  setDrawerTab:     (t: DrawerTab)    => void;
  setWorkspaceTab:  (t: WorkspaceTab) => void;
  setConnStatus:    (s: ConnStatus)   => void;
  openPalette:      () => void;
  closePalette:     () => void;
  togglePalette:    () => void;
}

export const useUIStore = create<UIState>((set) => ({
  sideActivity:  "chat",
  inspectorOpen: true,
  drawerOpen:    false,
  drawerTab:     "terminal",
  workspaceTab:  "chat",
  connStatus:    "connecting",
  paletteOpen:   false,

  setSideActivity: (a) => set({ sideActivity: a }),
  toggleSideActivity: (a) => set((s) => ({
    sideActivity: s.sideActivity === a ? null : a,
  })),
  toggleSidebar:   () => set((s) => ({
    sideActivity: s.sideActivity ? null : "chat",
  })),
  toggleInspector: () => set((s) => ({ inspectorOpen: !s.inspectorOpen })),
  toggleDrawer:    () => set((s) => ({ drawerOpen:    !s.drawerOpen })),
  openDrawer:      (tab) => set((s) => ({
    drawerOpen: true,
    drawerTab:  tab ?? s.drawerTab,
  })),
  setDrawerTab:    (t) => set({ drawerTab: t, drawerOpen: true }),
  setWorkspaceTab: (t) => set({ workspaceTab: t }),
  setConnStatus:   (s) => set({ connStatus: s }),
  openPalette:     ()  => set({ paletteOpen: true }),
  closePalette:    ()  => set({ paletteOpen: false }),
  togglePalette:   ()  => set((s) => ({ paletteOpen: !s.paletteOpen })),
}));
