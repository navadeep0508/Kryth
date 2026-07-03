import { memo } from "react";
import { ChevronRight, ChevronDown, Brain } from "lucide-react";
import { cn } from "@/lib/utils";
import { useWorkspaceStore, type ReasoningEvent } from "@/store/workspaceStore";

interface ReasoningCardProps {
  event: ReasoningEvent;
  turnId: string;
}

export const ReasoningCard = memo(function ReasoningCard({ event, turnId }: ReasoningCardProps) {
  const toggleReasoningCollapse = useWorkspaceStore((s) => s.toggleReasoningCollapse);

  const wordCount = event.content.split(/\s+/).filter(Boolean).length;
  const label = event.isStreaming
    ? `Thinking (${wordCount} words)…`
    : `Thought for ${wordCount} words`;

  return (
    <div className="ml-9">
      <div className="border-l-2 border-accent/30 rounded-r-md">
        {/* Header — always clickable */}
        <button
          onClick={() => toggleReasoningCollapse(turnId, event.id)}
          className="flex items-center gap-2 w-full px-3 py-1.5 text-left hover:bg-panel-hover/50 transition-colors duration-100 rounded-tr-md"
        >
          <span className="text-accent/60">
            {event.collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
          </span>
          <Brain size={11} className="text-accent/60" />
          <span className="text-[11px] text-dim">
            {event.collapsed ? label : "Reasoning"}
          </span>
          {event.isStreaming && (
            <span className="inline-flex gap-[2px] ml-1">
              <span className="w-1 h-1 rounded-full bg-accent/50 animate-thinking-1" />
              <span className="w-1 h-1 rounded-full bg-accent/50 animate-thinking-2" />
              <span className="w-1 h-1 rounded-full bg-accent/50 animate-thinking-3" />
            </span>
          )}
        </button>

        {/* Content */}
        {!event.collapsed && (
          <div className="px-4 pb-2.5">
            <div className={cn(
              "text-[11px] text-dim/80 leading-relaxed whitespace-pre-wrap font-mono",
              "max-h-[240px] overflow-y-auto"
            )}>
              {event.content}
              {event.isStreaming && (
                <span className="inline-block w-[2px] h-3 bg-accent/50 ml-0.5 rounded-sm animate-cursor-blink align-middle" />
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
});
