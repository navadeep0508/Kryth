import { create } from "zustand";

export type WorkspaceTab = "chat" | "editor" | "settings";
export type DrawerTab    = "terminal" | "logs";
export type ConnStatus   = "connecting" | "connected" | "disconnected";

interface UIState {
  sidebarOpen:   boolean;
  inspectorOpen: boolean;
  drawerOpen:    boolean;
  drawerTab:     DrawerTab;
  workspaceTab:  WorkspaceTab;
  connStatus:    ConnStatus;
  paletteOpen:   boolean;

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
  sidebarOpen:   true,
  inspectorOpen: true,
  drawerOpen:    false,
  drawerTab:     "terminal",
  workspaceTab:  "chat",
  connStatus:    "connecting",
  paletteOpen:   false,

  toggleSidebar:   () => set((s) => ({ sidebarOpen:   !s.sidebarOpen })),
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
