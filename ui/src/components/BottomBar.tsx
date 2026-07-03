import React from "react";
import { useStore, useSession } from "../hooks/useStore";

export function BottomBar() {
  const store = useStore();
  const session = useSession();
  const vp = store.viewport.snapshot();
  const ui = store.ui;

  return (
    <div className="kryth-bottom-bar">
      <span className="kryth-bottom-bar-item">
        {session.provider || "no provider"}
      </span>
      <span className="kryth-bottom-bar-item">
        {session.model || "no model"}
      </span>
      <span className="kryth-bottom-bar-item" style={{ marginLeft: "auto" }}>
        tok {formatNum(session.tokensIn)} in / {formatNum(session.tokensOut)} out
      </span>
      <span className="kryth-bottom-bar-item">
        {vp.visibleRows}×{vp.visibleCols}
      </span>
      <span
        className="kryth-bottom-bar-item"
        style={{ cursor: "pointer" }}
        onClick={() => store.toggleInspector()}
      >
        {ui.inspectorOpen ? "▸ inspector" : "◂ inspector"}
      </span>
      <span
        className="kryth-bottom-bar-item"
        style={{ cursor: "pointer" }}
        onClick={() => store.toggleSidebar()}
      >
        {ui.sidebarOpen ? "◂ sidebar" : "▸ sidebar"}
      </span>
    </div>
  );
}

function formatNum(n: number): string {
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}
