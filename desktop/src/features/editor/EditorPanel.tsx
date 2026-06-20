import { memo, lazy, Suspense } from "react";
import { TabBar } from "./TabBar";
import { useEditorStore } from "@/store/editorStore";

const MonacoEditorView = lazy(() =>
  import("./MonacoEditor").then((m) => ({ default: m.MonacoEditorView }))
);

export default memo(function EditorPanel() {
  const { tabs, activeTabId } = useEditorStore();
  const activeTab = tabs.find((t) => t.id === activeTabId);

  return (
    <div className="flex flex-col flex-1 overflow-hidden bg-bg">
      <TabBar />
      {activeTab ? (
        <div className="flex-1 overflow-hidden">
          <Suspense fallback={<EditorLoading />}>
            <MonacoEditorView
              key={activeTab.id}
              tabId={activeTab.id}
              path={activeTab.path}
              content={activeTab.content}
              language={activeTab.language}
            />
          </Suspense>
        </div>
      ) : (
        <EditorEmpty />
      )}
    </div>
  );
});

function EditorLoading() {
  return (
    <div className="flex items-center justify-center h-full text-muted text-sm">
      Loading editor…
    </div>
  );
}

function EditorEmpty() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-3 text-muted">
      <p className="text-sm">Open a file from the explorer</p>
      <p className="text-xs opacity-60">or ask KRYTH to create one</p>
    </div>
  );
}
