import React, { useCallback, useEffect, useState } from "react";
import { getStore } from "../store/runtimeStore";
import { LayoutEngine } from "../runtime/layout";
import { Sidebar } from "./components/Sidebar";
import { TerminalView } from "./components/TerminalView";
import { Inspector } from "./components/Inspector";
import { CommandBar } from "./components/CommandBar";
import { BottomBar } from "./components/BottomBar";

export function App() {
  const store = getStore(30, 100);
  const [layout] = useState(() => new LayoutEngine(1200, 800));

  useEffect(() => {
    const onResize = () => {
      layout.resize(window.innerWidth, window.innerHeight);
    };
    onResize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [layout]);

  const handleResizeStart = useCallback(
    (pane: "sidebar" | "inspector", e: React.MouseEvent) => {
      e.preventDefault();
      const startX = e.clientX;
      const startWidth =
        pane === "sidebar"
          ? layout.state.sidebar.defaultWidth
          : layout.state.inspector.defaultWidth;

      const onMove = (ev: globalThis.MouseEvent) => {
        const delta = ev.clientX - startX;
        let newWidth: number;
        if (pane === "sidebar") {
          newWidth = startWidth + delta;
        } else {
          newWidth = startWidth - delta;
        }
        layout.setPaneWidth(pane, newWidth);
        document.documentElement.style.setProperty(
          `--${pane === "sidebar" ? "sidebar" : "inspector"}-width`,
          `${newWidth}px`,
        );
      };

      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      };

      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    },
    [layout],
  );

  const dims = layout.getPaneDimensions("main");

  return (
    <div className="kryth-layout">
      <Sidebar />
      {layout.state.sidebar.visible && layout.state.sidebar.resizable && (
        <div
          className="kryth-separator"
          onMouseDown={(e) => handleResizeStart("sidebar", e)}
        />
      )}
      <div className="kryth-main">
        <TerminalView
          width={dims.width}
          height={dims.height - 48 - 24}
        />
        <CommandBar />
        <BottomBar />
      </div>
      {layout.state.inspector.visible && layout.state.inspector.resizable && (
        <div
          className="kryth-separator"
          onMouseDown={(e) => handleResizeStart("inspector", e)}
        />
      )}
      <Inspector />
    </div>
  );
}
