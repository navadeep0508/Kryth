import { memo } from "react";
import { Terminal, FileEdit, FileX, FilePlus, Search, Cpu } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ApprovalRequest } from "@/store/approvalStore";

const ICON_MAP: Record<string, React.ElementType> = {
  run_command:  Terminal,
  write_file:   FileEdit,
  delete_file:  FileX,
  create_file:  FilePlus,
  search_code:  Search,
};

const RISK_STYLES: Record<string, string> = {
  low:    "text-muted border-border",
  medium: "text-warning border-warning/30",
  high:   "text-error border-error/30",
};

export const ToolRequest = memo(function ToolRequest({
  request,
  onApprove,
  onReject,
}: {
  request: ApprovalRequest;
  onApprove: () => void;
  onReject: () => void;
}) {
  const Icon = ICON_MAP[request.tool ?? ""] ?? Cpu;

  return (
    <div className={cn("rounded-xl border p-3 space-y-2", RISK_STYLES[request.risk])}>
      <div className="flex items-start gap-2">
        <div className="w-7 h-7 rounded-lg bg-surface2 border border-border flex items-center justify-center shrink-0">
          <Icon size={13} className="text-muted" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm text-text leading-tight">{request.message}</p>
          {request.detail && (
            <p className="text-xs text-muted mt-0.5 font-mono truncate">{request.detail}</p>
          )}
        </div>
        <span
          className={cn(
            "text-[10px] px-1.5 py-0.5 rounded border shrink-0",
            RISK_STYLES[request.risk]
          )}
        >
          {request.risk}
        </span>
      </div>

      <div className="flex gap-2 justify-end">
        <button
          onClick={onReject}
          className="px-3 py-1 rounded-lg text-xs border border-border text-muted hover:text-text transition-colors duration-120"
        >
          Deny
        </button>
        <button
          onClick={onApprove}
          className={cn(
            "px-3 py-1 rounded-lg text-xs font-medium transition-colors duration-120",
            request.risk === "high"
              ? "bg-error/10 text-error hover:bg-error/20 border border-error/30"
              : "bg-accent text-bg hover:bg-accent/90"
          )}
        >
          Allow
        </button>
      </div>
    </div>
  );
});
