import { memo } from "react";
import { cn } from "@/lib/utils";

export interface StageState {
  index: number;
  name: string;
  status: "pending" | "active" | "done" | "failed";
  detail?: string;
}

const STAGE_NAMES = ["Task", "Scan", "Plan", "Execute", "Verify", "Done"];

function stageIndex(name: string): number {
  const idx = STAGE_NAMES.indexOf(name);
  return idx >= 0 ? idx : 99;
}

interface Props { stages: StageState[] }

export const PipelineProgress = memo(function PipelineProgress({ stages }: Props) {
  const sorted = [...stages].sort((a, b) => {
    const ai = a.index ?? stageIndex(a.name);
    const bi = b.index ?? stageIndex(b.name);
    return ai - bi;
  });

  return (
    <div className="flex flex-col gap-0">
      {STAGE_NAMES.map((name, i) => {
        const live = sorted.find((s) => s.name === name);
        const status = live?.status ?? (
          sorted.some((s) => stageIndex(s.name) > i && (s.status === "active" || s.status === "done"))
            ? "done"
            : "pending"
        );
        return <StageRow key={name} name={name} status={status} detail={live?.detail} />;
      })}
    </div>
  );
});

function StageRow({ name, status, detail }: { name: string; status: "pending" | "active" | "done" | "failed"; detail?: string }) {
  const glyph =
    status === "active" ? "◇" :
    status === "done" ? "✓" :
    status === "failed" ? "◆" :
    "○";

  return (
    <div
      className={cn(
        "flex items-center gap-1.5 px-2 py-1 transition-colors duration-120",
        status === "active" && "bg-accent/5 text-text",
        status === "done" && "text-success",
        status === "pending" && "text-faint",
        status === "failed" && "text-danger",
      )}
    >
      <span className={cn(
        "text-[11px] font-mono w-3 text-center shrink-0",
        status === "active" && "animate-pulse",
      )}>
        {glyph}
      </span>
      <span className="text-[11px] font-medium flex-1 truncate">{name}</span>
      {status === "active" && detail && (
        <span className="text-[9px] text-dim truncate max-w-[100px]">{detail}</span>
      )}
    </div>
  );
}
