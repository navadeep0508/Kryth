import type { PaneId, PaneConfig, PaneDimensions } from "./types";

export interface LayoutState {
  sidebar: PaneConfig;
  inspector: PaneConfig;
  main: PaneConfig;
  totalWidth: number;
  totalHeight: number;
  separatorWidth: number;
}

export class LayoutEngine {
  state: LayoutState;
  private _separatorHit = 6;

  constructor(totalWidth = 1200, totalHeight = 800) {
    this.state = {
      totalWidth,
      totalHeight,
      separatorWidth: 2,
      sidebar: {
        id: "sidebar",
        width: 240,
        minWidth: 180,
        maxWidth: 400,
        defaultWidth: 240,
        visible: true,
        resizable: true,
      },
      main: {
        id: "main",
        width: 0,
        minWidth: 400,
        maxWidth: 9999,
        defaultWidth: 0,
        visible: true,
        resizable: false,
      },
      inspector: {
        id: "inspector",
        width: 300,
        minWidth: 240,
        maxWidth: 500,
        defaultWidth: 300,
        visible: false,
        resizable: true,
      },
    };
  }

  resize(width: number, height: number): void {
    this.state.totalWidth = width;
    this.state.totalHeight = height;
    this._clampWidths();
  }

  togglePane(pane: "sidebar" | "inspector"): void {
    this.state[pane].visible = !this.state[pane].visible;
    if (this.state[pane].visible) {
      this.state[pane].width = this.state[pane].defaultWidth;
    }
    this._clampWidths();
  }

  setPaneWidth(pane: "sidebar" | "inspector", width: number): void {
    this.state[pane].width = Math.max(
      this.state[pane].minWidth,
      Math.min(this.state[pane].maxWidth, width),
    );
    this._clampWidths();
  }

  getPaneDimensions(pane: PaneId): PaneDimensions {
    const layout = this._compute();
    return layout[pane];
  }

  getSeparatorHitRegion(pane: "sidebar" | "inspector"): {
    x: number;
    width: number;
  } | null {
    if (!this.state[pane].visible) return null;
    const dims = this.getPaneDimensions(pane);
    const sepX =
      pane === "sidebar"
        ? dims.x + dims.width
        : dims.x - this.state.separatorWidth;
    return { x: sepX - this._separatorHit / 2, width: this._separatorHit };
  }

  isInSeparator(x: number, pane: "sidebar" | "inspector"): boolean {
    const region = this.getSeparatorHitRegion(pane);
    if (!region) return false;
    return x >= region.x && x <= region.x + region.width;
  }

  private _compute(): Record<PaneId, PaneDimensions> {
    const s = this.state;
    let x = 0;

    const sidebarW = s.sidebar.visible ? s.sidebar.defaultWidth : 0;
    const inspectorW = s.inspector.visible ? s.inspector.defaultWidth : 0;
    const sepW = s.separatorWidth;
    const sidebarSep = s.sidebar.visible && s.sidebar.resizable ? sepW : 0;
    const inspectorSep = s.inspector.visible && s.inspector.resizable ? sepW : 0;
    const mainW = Math.max(
      s.main.minWidth,
      s.totalWidth - sidebarW - inspectorW - sidebarSep - inspectorSep,
    );

    const sidebar: PaneDimensions = {
      x: 0,
      y: 0,
      width: sidebarW,
      height: s.totalHeight,
    };

    x = sidebarW + sidebarSep;

    const main: PaneDimensions = {
      x,
      y: 0,
      width: mainW,
      height: s.totalHeight,
    };

    x += mainW + inspectorSep;

    const inspector: PaneDimensions = {
      x,
      y: 0,
      width: inspectorW,
      height: s.totalHeight,
    };

    return { sidebar, main, inspector };
  }

  private _clampWidths(): void {
    const s = this.state;
    const minMain = s.main.minWidth;
    const available =
      s.totalWidth -
      (s.sidebar.visible ? s.sidebar.defaultWidth : 0) -
      (s.inspector.visible ? s.inspector.defaultWidth : 0);
    if (available < minMain) {
      const over = minMain - available;
      const visible = [];
      if (s.sidebar.visible) visible.push("sidebar");
      if (s.inspector.visible) visible.push("inspector");
      if (visible.length > 0) {
        const each = Math.ceil(over / visible.length);
        if (s.sidebar.visible) {
          s.sidebar.defaultWidth = Math.max(
            s.sidebar.minWidth,
            s.sidebar.defaultWidth - each,
          );
        }
        if (s.inspector.visible) {
          s.inspector.defaultWidth = Math.max(
            s.inspector.minWidth,
            s.inspector.defaultWidth - each,
          );
        }
      }
    }
  }

  snapshot() {
    return {
      layout: this._compute(),
      config: this.state,
    };
  }
}
