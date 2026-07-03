import { memo, useCallback, useState } from "react";
import { Loader2, Check, X, Clock, ChevronRight, FileCode, Terminal, Play, Search, Globe } from "lucide-react";
import { cn } from "@/lib/utils";
import { bridge } from "@/lib/krythBridge";
import { useEditorStore } from "@/store/editorStore";
import { useToastStore } from "@/store/toastStore";
import type { ToolCallEvent } from "@/store/workspaceStore";

const FILE_TOOLS = new Set(["write_file", "read_file", "create_file", "delete_file"]);

const TOOL_ICONS: Record<string, React.ElementType> = {
  write_file: FileCode,
  read_file: FileCode,
  create_file: FileCode,
  delete_file: FileCode,
  run_command: Terminal,
  execute: Terminal,
  shell: Terminal,
  search_code: Search,
  grep_search: Search,
  web_search: Globe,
};

interface ToolCardProps {
  event: ToolCallEvent;
  compact?: boolean;
}

export const ToolCard = memo(function ToolCard({ event, compact }: ToolCardProps) {
  const [expanded, setExpanded] = useState(false);
  const filePath = FILE_TOOLS.has(event.tool) ? extractFilePath(event.args) : null;
  const Icon = TOOL_ICONS[event.tool] ?? Play;

  const handlePathClick = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!filePath) return;
    try {
      const content = await bridge.readFile(filePath);
      const filename = filePath.split(/[/\\]/).pop() ?? filePath;
      useEditorStore.getState().openTab({ path: filePath, filename, content, language: "" });
    } catch (err) {
      useToastStore.getState().addToast(
        `Failed to open file: ${err instanceof Error ? err.message : "Unknown error"}`,
        "error"
      );
    }
  }, [filePath]);

  const label = humanLabel(event.tool, event.args);

  // Compact mode: single-line, no expand
  if (compact) {
    return (
      <div className={cn(
        "flex items-center gap-2 px-3 py-1.5 rounded-md transition-colors duration-100",
        event.status === "running" && "bg-accent/[0.03]",
        event.status === "success" && "bg-transparent",
        event.status === "failed" && "bg-danger/[0.03]",
      )}>
        <StatusDot status={event.status} />
        <Icon size={12} className="text-dim shrink-0" />
        <span className="text-xs font-mono text-muted truncate">{event.tool}</span>
        {label && <span className="text-xs text-dim truncate">{label}</span>}
        {event.runtime != null && (
          <span className="text-[10px] text-faint font-mono ml-auto">{event.runtime.toFixed(1)}s</span>
        )}
      </div>
    );
  }

  return (
    <div className={cn(
      "rounded-lg border overflow-hidden transition-all duration-150",
      event.status === "running" && "border-accent/20 bg-accent/[0.02]",
      event.status === "success" && "border-border-soft bg-panel/50",
      event.status === "failed" && "border-danger/20 bg-danger/[0.02]",
      event.status === "queued" && "border-border-soft bg-panel/30",
    )}>
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2.5 px-3 py-2 text-left hover:bg-panel-hover/30 transition-colors duration-100"
      >
        <ChevronRight
          size={11}
          className={cn(
            "text-dim shrink-0 transition-transform duration-150",
            expanded && "rotate-90"
          )}
        />
        <StatusDot status={event.status} />
        <Icon size={12} className="text-muted shrink-0" />
        <span className="text-xs font-mono font-medium text-text">{event.tool}</span>
        {filePath && (
          <button
            onClick={handlePathClick}
            className="text-xs font-mono text-accent hover:underline truncate max-w-[200px]"
            title={filePath}
          >
            {shortenPath(filePath)}
          </button>
        )}
        {!filePath && label && (
          <span className="text-xs text-dim truncate max-w-[200px]">{label}</span>
        )}
        <div className="flex-1" />
        {event.status === "running" && (
          <Loader2 size={11} className="animate-spin text-accent shrink-0" />
        )}
        {event.runtime != null && (
          <span className="text-[10px] text-faint font-mono">{event.runtime.toFixed(1)}s</span>
        )}
      </button>

      {/* Expanded body */}
      {expanded && (
        <div className="border-t border-border-soft">
          {Object.keys(event.args).length > 0 && (
            <div className="px-3 py-2">
              <span className="text-[10px] font-semibold text-dim uppercase tracking-wider">Args</span>
              <pre className="mt-1 text-xs font-mono text-muted leading-relaxed whitespace-pre-wrap break-all max-h-[180px] overflow-y-auto">
                {formatArgs(event.args)}
              </pre>
            </div>
          )}
          {event.result && event.status !== "running" && (
            <div className="px-3 py-2 border-t border-border-soft">
              <span className={cn(
                "text-[10px] font-semibold uppercase tracking-wider",
                event.status === "success" ? "text-dim" : "text-danger"
              )}>
                {event.status === "success" ? "Output" : "Error"}
              </span>
              <pre className={cn(
                "mt-1 text-xs font-mono leading-relaxed whitespace-pre-wrap break-all max-h-[200px] overflow-y-auto",
                event.status === "success" ? "text-muted" : "text-danger/80"
              )}>
                {event.result.length > 800 ? event.result.slice(0, 800) + "\n…(truncated)" : event.result}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
});

/* ── Status dot ────────────────────────────────────────────────── */
function StatusDot({ status }: { status: string }) {
  return (
    <span className={cn(
      "w-2 h-2 rounded-full shrink-0",
      status === "running" && "bg-accent animate-pulse",
      status === "success" && "bg-success",
      status === "failed" && "bg-danger",
      status === "queued" && "bg-dim/40",
    )} />
  );
}

/* ── Helpers ───────────────────────────────────────────────────── */
function extractFilePath(args: Record<string, unknown>): string | null {
  const path = args.path ?? args.file_path ?? args.filepath ?? args.file ?? args.filename;
  if (typeof path === "string" && path.length > 0) return path;
  return null;
}

function shortenPath(fullPath: string): string {
  const parts = fullPath.replace(/\\/g, "/").split("/");
  if (parts.length <= 3) return parts.join("/");
  return `…/${parts.slice(-2).join("/")}`;
}

function humanLabel(tool: string, args: Record<string, unknown>): string {
  const cmd = args.command ?? args.cmd;
  if (typeof cmd === "string") {
    const first = cmd.split(/[;&|]/, 1)[0].trim();
    return first.length > 45 ? first.slice(0, 45) + "…" : first;
  }
  const query = args.query ?? args.search ?? args.pattern;
  if (typeof query === "string") {
    return `"${query.length > 35 ? query.slice(0, 35) + "…" : query}"`;
  }
  return "";
}

function formatArgs(args: Record<string, unknown>): string {
  const lines: string[] = [];
  for (const [key, val] of Object.entries(args)) {
    if (typeof val === "string") {
      if (val.length > 150 || val.includes("\n")) {
        lines.push(`${key}:`);
        const preview = val.slice(0, 300);
        lines.push(`  ${preview}${val.length > 300 ? "\n  …" : ""}`);
      } else {
        lines.push(`${key}: "${val}"`);
      }
    } else {
      lines.push(`${key}: ${JSON.stringify(val)}`);
    }
  }
  return lines.join("\n");
}
