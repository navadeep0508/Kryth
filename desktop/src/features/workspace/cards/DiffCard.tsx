import { memo } from "react";
import { cn } from "@/lib/utils";
import { useWorkspaceStore, type DiffEvent } from "@/store/workspaceStore";
import { Check, X, FileDiff, FileCode } from "lucide-react";

export const DiffCard = memo(function DiffCard({ event, turnId }: { event: DiffEvent; turnId: string }) {
  const applyDiff = useWorkspaceStore((s) => s.applyDiff);
  const rejectDiff = useWorkspaceStore((s) => s.rejectDiff);

  const filename = event.path.replace(/\\/g, "/").split("/").pop() ?? event.path;
  const dirParts = event.path.replace(/\\/g, "/").split("/");
  const shortDir = dirParts.length > 3 ? `…/${dirParts.slice(-3, -1).join("/")}` : dirParts.slice(0, -1).join("/");

  return (
    <div className="rounded-lg border border-border-soft overflow-hidden animate-slide-up">
      {/* File header */}
      <div className="flex items-center gap-2 px-3 py-2 bg-[#F6F8FA] border-b border-border-soft">
        <FileCode size={12} className="text-muted shrink-0" />
        <span className="text-xs font-mono font-medium text-text">{filename}</span>
        <span className="text-[10px] text-dim truncate">{shortDir}</span>
        <div className="flex-1" />
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-success font-mono font-medium">+{event.additions}</span>
          <span className="text-[10px] text-danger font-mono font-medium">-{event.deletions}</span>
        </div>
      </div>

      {/* Diff lines */}
      {event.hunks.length > 0 && (
        <div className="max-h-[300px] overflow-y-auto font-mono text-[11.5px] leading-[1.6]">
          {event.hunks.map((hunk, hi) => (
            <div key={hi}>
              <div className="px-3 py-0.5 bg-[#F6F8FA] text-[10px] text-dim border-y border-border-soft/50 select-none">
                {hunk.header}
              </div>
              {hunk.lines.map((line, li) => (
                <div
                  key={li}
                  className={cn(
                    "px-3 flex",
                    line.type === "add" && "bg-[#DAFBE1] border-l-[3px] border-l-success",
                    line.type === "del" && "bg-[#FFEBE9] border-l-[3px] border-l-danger",
                    line.type === "ctx" && "border-l-[3px] border-l-transparent",
                  )}
                >
                  {line.lineNum != null && (
                    <span className="w-8 shrink-0 text-right pr-2 text-dim/60 select-none">
                      {line.lineNum}
                    </span>
                  )}
                  <span className="w-4 shrink-0 text-dim/60 select-none text-center">
                    {line.type === "add" ? "+" : line.type === "del" ? "−" : " "}
                  </span>
                  <span className={cn(
                    "flex-1",
                    line.type === "add" && "text-[#116329]",
                    line.type === "del" && "text-[#82071E]",
                    line.type === "ctx" && "text-muted",
                  )}>
                    {line.content}
                  </span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {/* Actions */}
      {event.status === "pending" && (
        <div className="flex items-center justify-end gap-2 px-3 py-2 border-t border-border-soft bg-[#F6F8FA]">
          <button
            onClick={() => rejectDiff(turnId, event.id)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs border border-border text-muted hover:text-danger hover:border-danger/30 transition-colors duration-100"
          >
            <X size={11} /> Reject
          </button>
          <button
            onClick={() => applyDiff(turnId, event.id)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium bg-success text-white hover:bg-success/90 transition-colors duration-100"
          >
            <Check size={11} /> Apply
          </button>
        </div>
      )}

      {event.status === "applied" && (
        <div className="flex items-center gap-1.5 px-3 py-1.5 border-t border-success/20 bg-success/[0.03] text-[10px] text-success font-medium">
          <Check size={10} /> Changes applied
        </div>
      )}

      {event.status === "rejected" && (
        <div className="flex items-center gap-1.5 px-3 py-1.5 border-t border-danger/20 bg-danger/[0.03] text-[10px] text-danger font-medium">
          <X size={10} /> Changes rejected
        </div>
      )}
    </div>
  );
});
