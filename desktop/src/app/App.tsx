import React from "react";
import { MainLayout } from "@/layouts/MainLayout";
import { useKrythEvents } from "@/hooks/useKrythEvents";
import { useUIStore } from "@/store/uiStore";
import { useWorkspaceStore } from "@/store/workspaceStore";
import { useProjectStore } from "@/store/projectStore";

function AppInner() {
  useKrythEvents();

  // Restore last opened folder on app boot — ensures agent always has a valid cwd
  React.useEffect(() => {
    const { cwd, restoreLastFolder } = useProjectStore.getState();
    if (!cwd) {
      restoreLastFolder();
    }
  }, []);

  React.useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const ctrl = e.ctrlKey || e.metaKey;

      // Ctrl+K → command palette
      if (ctrl && e.key === "k") {
        e.preventDefault();
        useUIStore.getState().openPalette();
        return;
      }

      // Escape → close palette
      if (e.key === "Escape") {
        useUIStore.getState().closePalette();
        return;
      }

      // Ctrl+` or Ctrl+J → toggle bottom dock (terminal)
      if (ctrl && (e.key === "`" || e.key === "j")) {
        e.preventDefault();
        useUIStore.getState().toggleDock();
        return;
      }

      // Ctrl+B → toggle sidebar
      if (ctrl && e.key === "b") {
        e.preventDefault();
        useUIStore.getState().toggleSidebar();
        return;
      }

      // Ctrl+L → focus prompt input
      if (ctrl && e.key === "l") {
        e.preventDefault();
        document.dispatchEvent(new CustomEvent("kryth:focus-prompt"));
        return;
      }

      // Ctrl+Shift+R → toggle right panel
      if (ctrl && e.shiftKey && e.key === "R") {
        e.preventDefault();
        useUIStore.getState().toggleRightPanel();
        return;
      }

      // Ctrl+Shift+N → new conversation
      if (ctrl && e.shiftKey && e.key === "N") {
        e.preventDefault();
        useWorkspaceStore.getState().clearAll();
        document.dispatchEvent(new CustomEvent("kryth:focus-prompt"));
        return;
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return <MainLayout />;
}

class AppErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean; error: string }
> {
  state = { hasError: false, error: "" };

  static getDerivedStateFromError(e: Error) {
    return { hasError: true, error: e.message };
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="h-screen w-screen flex flex-col items-center justify-center bg-bg text-text gap-4">
          <h1 className="text-lg font-semibold">KRYTH encountered an error</h1>
          <p className="text-sm font-mono text-danger max-w-md text-center">{this.state.error}</p>
          <button
            onClick={() => this.setState({ hasError: false, error: "" })}
            className="px-4 py-2 rounded-md bg-accent text-white text-sm font-medium hover:bg-accent/80 transition-colors"
          >
            Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export function App() {
  return (
    <AppErrorBoundary>
      <AppInner />
    </AppErrorBoundary>
  );
}
