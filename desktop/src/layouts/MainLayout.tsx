import { lazy, Suspense, memo } from "react";
import { useUIStore } from "@/store/uiStore";
import { useEditorStore } from "@/store/editorStore";
import { AppShell } from "./AppShell";
import { Sidebar } from "./Sidebar";
import { PromptInput } from "./PromptInput";
import { BottomDock } from "./BottomDock";
import { RightPanel } from "@/components/RightPanel";
import { TopBar } from "./TopBar";
import { PinnedPlan } from "@/features/workspace/Workspace";
import { ApprovalBar } from "@/features/workspace/ApprovalBar";
import { ToastContainer } from "@/features/toast/ToastContainer";

const Workspace = lazy(() => import("@/features/workspace/Workspace"));
const EditorPanel = lazy(() => import("@/features/editor/EditorPanel"));
const CommandPalette = lazy(() => import("@/features/palette/CommandPalette"));
const SettingsPage = lazy(() => import("@/features/settings/SettingsPage"));

export const MainLayout = memo(function MainLayout() {
  const { centerView, paletteOpen, dockOpen } = useUIStore();
  const tabs = useEditorStore((s) => s.tabs);
  const hasEditor = tabs.length > 0;

  const centerContent = centerView === "settings" ? (
    <Suspense fallback={<LoadingState>Loading settings…</LoadingState>}>
      <SettingsPage />
    </Suspense>
  ) : hasEditor ? (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-hidden">
        <Suspense fallback={<LoadingState>Loading editor…</LoadingState>}>
          <EditorPanel />
        </Suspense>
      </div>
    </div>
  ) : (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-hidden">
        <Suspense fallback={<LoadingState>Loading workspace…</LoadingState>}>
          <Workspace />
        </Suspense>
      </div>
      <PinnedPlan />
      <ApprovalBar />
    </div>
  );

  return (
    <>
      <AppShell
        topBar={<TopBar />}
        sidebar={<Sidebar />}
        center={centerContent}
        inspector={<RightPanel />}
        commandBar={<PromptInput />}
        bottomDock={dockOpen ? <BottomDock /> : null}
      />
      {paletteOpen && (
        <Suspense fallback={null}>
          <CommandPalette />
        </Suspense>
      )}
      <ToastContainer />
    </>
  );
});

function LoadingState({ children }: { children: React.ReactNode }) {
  return (
    <div className="h-full flex items-center justify-center text-dim text-xs">
      {children}
    </div>
  );
}
