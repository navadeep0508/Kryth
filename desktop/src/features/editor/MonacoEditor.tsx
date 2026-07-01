import { memo, useCallback } from "react";
import MonacoReact from "@monaco-editor/react";
import { useEditorStore } from "@/store/editorStore";
import { useToastStore } from "@/store/toastStore";
import { bridge } from "@/lib/krythBridge";
import { monacoTheme } from "@/lib/theme";
import type { editor as MonacoEditor } from "monaco-editor";

interface Props {
  tabId: string;
  path: string;
  content: string;
  language: string;
}

export const MonacoEditorView = memo(function MonacoEditorView({ tabId, path, content, language }: Props) {
  const updateContent = useEditorStore((s) => s.updateContent);
  const markSaved     = useEditorStore((s) => s.markSaved);

  const handleMount = useCallback(
    (editor: MonacoEditor.IStandaloneCodeEditor, monaco: typeof import("monaco-editor")) => {
      monaco.editor.defineTheme("kryth-dark", monacoTheme as MonacoEditor.IStandaloneThemeData);
      monaco.editor.setTheme("kryth-dark");

      editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
        const currentContent = editor.getValue();
        bridge.writeFile(path, currentContent)
          .then(() => {
            markSaved(tabId);
            useToastStore.getState().addToast("File saved", "success");
          })
          .catch((err) => {
            useToastStore.getState().addToast(
              `Save failed: ${err instanceof Error ? err.message : "Unknown error"}`,
              "error"
            );
          });
      });
    },
    [tabId, path, markSaved]
  );

  return (
    <MonacoReact
      height="100%"
      language={language}
      value={content}
      theme="kryth-dark"
      onChange={(val) => updateContent(tabId, val ?? "")}
      onMount={handleMount}
      options={{
        fontSize: 13,
        fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', Menlo, monospace",
        fontLigatures: true,
        lineHeight: 20,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        renderLineHighlight: "gutter",
        cursorBlinking: "smooth",
        smoothScrolling: true,
        formatOnPaste: true,
        tabSize: 2,
        wordWrap: "on",
        padding: { top: 12, bottom: 12 },
        overviewRulerLanes: 0,
        hideCursorInOverviewRuler: true,
        scrollbar: { verticalScrollbarSize: 4, horizontalScrollbarSize: 4 },
      }}
    />
  );
});
