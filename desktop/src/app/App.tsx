import React from "react";
import { MainLayout } from "@/layouts/MainLayout";
import { useKrythEvents } from "@/hooks/useKrythEvents";
import { useUIStore } from "@/store/uiStore";

function AppInner() {
  useKrythEvents();

  // Global keyboard shortcuts
  React.useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key === "k") {
        e.preventDefault();
        useUIStore.getState().openPalette();
      }
      if (e.key === "Escape") {
        useUIStore.getState().closePalette();
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
          <p className="text-sm font-mono text-error max-w-md text-center">{this.state.error}</p>
          <button
            onClick={() => this.setState({ hasError: false, error: "" })}
            className="px-4 py-2 rounded-lg bg-accent text-bg text-sm font-medium"
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
