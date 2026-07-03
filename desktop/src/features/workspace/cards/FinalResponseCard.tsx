import { memo, useCallback } from "react";
import { CheckCircle2, FileText, ArrowRight, Terminal } from "lucide-react";
import { bridge } from "@/lib/krythBridge";
import { useEditorStore } from "@/store/editorStore";
import { useToastStore } from "@/store/toastStore";
import type { FinalResponseEvent } from "@/store/workspaceStore";

export const FinalResponseCard = memo(function FinalResponseCard({ event }: { event: FinalResponseEvent }) {
  return (
    <div className="ml-9 rounded-lg border border-success/20 bg-gradient-to-b from-success/[0.02] to-transparent overflow-hidden">
      {/* Summary header */}
      <div className="px-4 py-3">
        <div className="flex items-center gap-2 mb-2">
          <CheckCircle2 size={14} className="text-success" />
          <span className="text-xs font-semibold text-success">Done</span>
        </div>
        <p className="text-[13px] text-text leading-relaxed">{event.summary}</p>
      </div>

      {/* Files modified */}
      {event.filesModified.length > 0 && (
        <div className="px-4 py-2 border-t border-border-soft/50">
          <div className="flex flex-wrap gap-1.5">
            {event.filesModified.map((file) => (
              <FileChip key={file} path={file} />
            ))}
          </div>
        </div>
      )}

      {/* Next steps */}
      {event.nextSteps.length > 0 && (
        <div className="px-4 py-2 border-t border-border-soft/50">
          {event.nextSteps.map((step, i) => (
            <div key={i} className="flex items-start gap-2 py-0.5">
              <ArrowRight size={10} className="text-accent shrink-0 mt-0.5" />
              <span className="text-xs text-muted">{step}</span>
            </div>
          ))}
        </div>
      )}

      {/* Commands to run */}
      {event.commands && event.commands.length > 0 && (
        <div className="px-4 py-2 border-t border-border-soft/50 space-y-1">
          {event.commands.map((cmd, i) => (
            <div key={i} className="flex items-center gap-2 px-2.5 py-1.5 rounded bg-[#0D1117]">
              <Terminal size={10} className="text-[#8B949E] shrink-0" />
              <code className="text-[11px] font-mono text-[#E6EDF3]">{cmd}</code>
            </div>
          ))}
        </div>
      )}
    </div>
  );
});

const FileChip = memo(function FileChip({ path }: { path: string }) {
  const handleClick = useCallback(async () => {
    try {
      const content = await bridge.readFile(path);
      const filename = path.split(/[/\\]/).pop() ?? path;
      useEditorStore.getState().openTab({ path, filename, content, language: "" });
    } catch (err) {
      useToastStore.getState().addToast(
        `Failed to open: ${err instanceof Error ? err.message : "Unknown error"}`,
        "error"
      );
    }
  }, [path]);

  const filename = path.replace(/\\/g, "/").split("/").pop() ?? path;

  return (
    <button
      onClick={handleClick}
      className="inline-flex items-center gap-1.5 px-2 py-1 rounded bg-panel border border-border-soft hover:border-accent/30 text-[11px] font-mono text-muted hover:text-accent transition-colors cursor-pointer"
    >
      <FileText size={10} className="shrink-0" />
      {filename}
    </button>
  );
});
