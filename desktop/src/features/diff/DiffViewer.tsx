import { memo } from "react";
import { useWorkspaceStore, type DiffEvent } from "@/store/workspaceStore";

export default memo(function DiffViewer() {
  const turns = useWorkspaceStore((s) => s.turns);

  const allDiffs: { turnId: string; event: DiffEvent }[] = [];
  for (const turn of turns) {
    for (const evt of turn.events) {
      if (evt.type === "diff") {
        allDiffs.push({ turnId: turn.id, event: evt });
      }
    }
  }

  if (allDiffs.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 text-dim p-4">
        <p className="text-xs">No pending diffs</p>
        <p className="text-2xs opacity-60">File changes will appear here</p>
      </div>
    );
  }

  const latest = allDiffs[allDiffs.length - 1];
  const { event } = latest;
  const filename = event.path.replace(/\\/g, "/").split("/").pop() ?? event.path;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b border-border-soft shrink-0">
        <span className="text-xs font-mono text-text truncate">{filename}</span>
        <div className="flex items-center gap-2 text-2xs font-mono">
          <span className="text-success">+{event.additions}</span>
          <span className="text-danger">-{event.deletions}</span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto font-mono text-xs leading-5">
        {event.hunks.map((hunk, hi) => (
          <div key={hi}>
            <div className="px-3 py-0.5 bg-panel-hover text-dim text-2xs">
              {hunk.header}
            </div>
            {hunk.lines.map((line, li) => (
              <div
                key={li}
                className={
                  line.type === "add" ? "diff-add px-3" :
                  line.type === "del" ? "diff-del px-3" :
                  "px-3"
                }
              >
                <span className="text-dim select-none inline-block w-3">
                  {line.type === "add" ? "+" : line.type === "del" ? "-" : " "}
                </span>
                <span className={
                  line.type === "add" ? "text-success" :
                  line.type === "del" ? "text-danger" :
                  "text-muted"
                }>
                  {line.content}
                </span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
});
