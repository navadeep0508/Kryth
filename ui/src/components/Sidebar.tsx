import React from "react";
import { useStore } from "../hooks/useStore";

export function Sidebar() {
  const store = useStore();
  const ui = store.ui;

  if (!ui.sidebarOpen) return null;

  return (
    <div className="kryth-sidebar">
      <div className="kryth-sidebar-header">
        <div className="kryth-logo">KRYTH</div>
        <div className="kryth-logo-sub">AI Runtime v2</div>
      </div>

      <div className="kryth-sidebar-section">
        <div className="kryth-sidebar-section-title">Workspace</div>
        <div className="kryth-sidebar-item active">
          ~/Documents/Kryth
        </div>
      </div>

      <div className="kryth-sidebar-section">
        <div className="kryth-sidebar-section-title">Git</div>
        <div className="kryth-sidebar-item">main</div>
      </div>

      <div className="kryth-sidebar-section">
        <div className="kryth-sidebar-section-title">Sessions</div>
        <div className="kryth-sidebar-item active">Current session</div>
      </div>

      <div className="kryth-sidebar-section" style={{ flex: 1, overflow: "auto" }}>
        <div className="kryth-sidebar-section-title">Files</div>
        <div className="kryth-sidebar-item">src/</div>
        <div className="kryth-sidebar-item">backend/</div>
        <div className="kryth-sidebar-item">frontend/</div>
        <div className="kryth-sidebar-item">ui/</div>
        <div className="kryth-sidebar-item">package.json</div>
        <div className="kryth-sidebar-item">tsconfig.json</div>
      </div>

      <div className="kryth-sidebar-section" style={{ borderTop: "1px solid var(--border)" }}>
        <div className="kryth-sidebar-item">
          {store.session.provider || "No provider"}
        </div>
      </div>
    </div>
  );
}
