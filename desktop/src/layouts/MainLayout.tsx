import React, { lazy, Suspense } from "react";
import { TopBar } from "./TopBar";
import { Sidebar } from "./Sidebar";
import { InspectorPanel } from "./InspectorPanel";
import { BottomDrawer } from "./BottomDrawer";
import { useUIStore } from "@/store/uiStore";
import { useApprovalStore } from "@/store/approvalStore";

const ChatPanel      = lazy(() => import("@/features/chat/ChatPanel"));
const EditorPanel    = lazy(() => import("@/features/editor/EditorPanel"));
const FileExplorer   = lazy(() => import("@/features/explorer/FileExplorer"));
const ApprovalModal  = lazy(() => import("@/features/approvals/ApprovalModal"));
const CommandPalette = lazy(() => import("@/features/palette/CommandPalette"));
const SettingsPage   = lazy(() => import("@/features/settings/SettingsPage"));

export function MainLayout() {
  const { workspaceTab, paletteOpen } = useUIStore();
  const hasPending = useApprovalStore((s) => s.pending.length > 0);

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-bg text-text">
      <TopBar />

      <div className="flex flex-1 overflow-hidden">
        <Sidebar />

        {/* Main workspace */}
        <main className="flex-1 flex overflow-hidden">
          <WorkspaceFallback>
            <Suspense fallback={<WorkspaceLoading />}>
              {workspaceTab === "chat"     && <ChatPanel />}
              {workspaceTab === "editor"   && (
                <div className="flex flex-1 overflow-hidden">
                  <FileExplorer />
                  <EditorPanel />
                </div>
              )}
              {workspaceTab === "settings" && <SettingsPage />}
            </Suspense>
          </WorkspaceFallback>
        </main>

        <InspectorPanel />
      </div>

      <BottomDrawer />

      {/* Overlays */}
      {hasPending && (
        <Suspense fallback={null}>
          <ApprovalModal />
        </Suspense>
      )}

      {paletteOpen && (
        <Suspense fallback={null}>
          <CommandPalette />
        </Suspense>
      )}
    </div>
  );
}

function WorkspaceLoading() {
  return (
    <div className="flex-1 flex items-center justify-center text-muted text-sm">
      Loading…
    </div>
  );
}

class WorkspaceFallback extends React.Component<
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
        <div className="flex-1 flex flex-col items-center justify-center gap-3 text-muted">
          <p className="text-sm">Something went wrong in the workspace.</p>
          <p className="text-xs font-mono text-error">{this.state.error}</p>
          <button
            onClick={() => this.setState({ hasError: false, error: "" })}
            className="text-xs text-accent underline"
          >
            Reload panel
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
