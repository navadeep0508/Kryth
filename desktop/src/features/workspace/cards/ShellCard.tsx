import { memo, useRef, useEffect } from "react";
import { Terminal, Check, X, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ShellEvent } from "@/store/workspaceStore";

export const ShellCard = memo(function ShellCard({ event }: { event: ShellEvent }) {
  const outputRef = useRef<HTMLPreElement>(null);
  const autoScrollRef = useRef(true);

  useEffect(() => {
    if (autoScrollRef.current && outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [event.output]);

  const handleScroll = () => {
    if (!outputRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = outputRef.current;
    autoScrollRef.current = scrollHeight - scrollTop - clientHeight < 40;
  };

  const hasError = !event.isRunning && event.exitCode != null && event.exitCode !== 0;

  return (
    <div className={cn(
      "rounded-lg border overflow-hidden transition-colors duration-150",
      event.isRunning && "border-accent/20",
      !event.isRunning && !hasError && "border-border-soft",
      hasError && "border-danger/20",
    )}>
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 bg-[#1B1F23]">
        <Terminal size={12} className="text-[#8B949E] shrink-0" />
        <span className="text-xs font-mono text-[#E6EDF3] truncate flex-1">
          {event.command}
        </span>
        {event.isRunning && (
          <Loader2 size={11} className="animate-spin text-accent shrink-0" />
        )}
        {!event.isRunning && event.exitCode != null && (
          <span className={cn(
            "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-medium",
            event.exitCode === 0 ? "text-[#3FB950]" : "text-[#F85149]"
          )}>
            {event.exitCode === 0 ? <Check size={9} /> : <X size={9} />}
            exit {event.exitCode}
          </span>
        )}
        {event.runtime != null && (
          <span className="text-[10px] text-[#8B949E] font-mono">{event.runtime.toFixed(1)}s</span>
        )}
      </div>

      {/* Output */}
      {(event.output || event.isRunning) && (
        <pre
          ref={outputRef}
          onScroll={handleScroll}
          className="px-3 py-2 text-xs font-mono leading-relaxed whitespace-pre-wrap break-all overflow-y-auto bg-[#0D1117] text-[#E6EDF3]"
          style={{ maxHeight: "280px" }}
        >
          {event.output ? renderOutput(event.output) : (
            <span className="text-[#8B949E] animate-pulse">Waiting for output...</span>
          )}
        </pre>
      )}
    </div>
  );
});

function renderOutput(output: string) {
  const lines = output.split("\n");
  return lines.map((line, i) => {
    const isStderr = line.startsWith("\x02stderr:");
    const text = isStderr ? line.slice(8) : line;
    return (
      <span key={i} className={isStderr ? "text-[#F85149]" : undefined}>
        {text}
        {i < lines.length - 1 ? "\n" : ""}
      </span>
    );
  });
}
