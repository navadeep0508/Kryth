import { memo, useState, useRef, useCallback, useEffect } from "react";
import { ArrowUp, Square, Paperclip } from "lucide-react";
import { cn } from "@/lib/utils";
import { useUIStore } from "@/store/uiStore";
import { useWorkspaceStore } from "@/store/workspaceStore";

export const PromptInput = memo(function PromptInput() {
  const [value, setValue] = useState("");
  const [attachedFiles, setAttachedFiles] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const agentStatus = useUIStore((s) => s.agentStatus);
  const execMode = useUIStore((s) => s.execMode);
  const setExecMode = useUIStore((s) => s.setExecMode);
  const sendPrompt = useWorkspaceStore((s) => s.sendPrompt);
  const interrupt = useWorkspaceStore((s) => s.interrupt);

  useEffect(() => { inputRef.current?.focus(); }, []);
  useEffect(() => {
    const handler = () => inputRef.current?.focus();
    document.addEventListener("kryth:focus-prompt", handler);
    return () => document.removeEventListener("kryth:focus-prompt", handler);
  }, []);

  const isRunning = agentStatus !== "idle" && agentStatus !== "done" && agentStatus !== "error";

  const handleAttach = useCallback(async () => {
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const selected = await open({ multiple: true, title: "Attach files" });
      if (selected) {
        const paths = Array.isArray(selected) ? selected : [selected];
        setAttachedFiles((prev) => [...new Set([...prev, ...paths.filter((p): p is string => typeof p === "string")])]);
      }
    } catch { console.warn("[PromptInput] Dialog unavailable"); }
  }, []);

  const handleSubmit = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed || isRunning) return;
    let prompt = trimmed;
    if (attachedFiles.length > 0) {
      prompt = attachedFiles.map((f) => `[attached: ${f}]`).join("\n") + "\n\n" + trimmed;
      setAttachedFiles([]);
    }
    sendPrompt(prompt);
    setValue("");
  }, [value, isRunning, sendPrompt, attachedFiles]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit]
  );

  return (
    <div className="border-t border-border bg-surface px-2 py-1.5 shrink-0">
      {/* Attached files */}
      {attachedFiles.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-1">
          {attachedFiles.map((file) => (
            <span key={file} className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-panel border border-border text-[10px] text-muted font-mono">
              <Paperclip size={8} />
              <span className="max-w-[100px] truncate">{file.split(/[/\\]/).pop()}</span>
              <button onClick={() => setAttachedFiles((p) => p.filter((f) => f !== file))} className="text-dim hover:text-danger">x</button>
            </span>
          ))}
        </div>
      )}

      <div className="flex items-center gap-1 border border-border bg-panel px-2 py-1 focus-within:border-accent/40 transition-colors duration-100">
        <input
          ref={inputRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="> what do you want to build?"
          className="flex-1 bg-transparent text-[12px] text-text font-mono placeholder:text-faint outline-none h-6"
          disabled={isRunning}
        />

        {/* Mode indicator */}
        <button
          onClick={() => {
            const modes: Array<typeof execMode> = ["auto", "fast", "deep", "max"];
            const idx = modes.indexOf(execMode);
            setExecMode(modes[(idx + 1) % modes.length]);
          }}
          className="text-[9px] font-mono text-faint hover:text-muted px-1 border border-border/50 transition-colors"
          title="Execution mode"
        >
          {execMode.toUpperCase()}
        </button>

        <button
          onClick={handleAttach}
          className="p-0.5 text-dim hover:text-muted transition-colors"
          title="Attach file"
        >
          <Paperclip size={11} />
        </button>

        {isRunning ? (
          <button
            onClick={interrupt}
            className="p-0.5 text-danger hover:text-danger/80 transition-colors"
            title="Stop"
          >
            <Square size={11} />
          </button>
        ) : (
          <button
            onClick={handleSubmit}
            disabled={!value.trim()}
            className={cn(
              "p-0.5 transition-colors",
              value.trim() ? "text-accent hover:text-accent/80" : "text-faint cursor-not-allowed"
            )}
            title="Send"
          >
            <ArrowUp size={11} />
          </button>
        )}
      </div>
    </div>
  );
});
