import { memo } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";

interface ErrorCardProps {
  onRetry: () => void;
}

export const ErrorCard = memo(function ErrorCard({ onRetry }: ErrorCardProps) {
  return (
    <div className="rounded-lg border border-danger/20 bg-danger/[0.03] overflow-hidden animate-slide-up ml-9">
      <div className="px-4 py-3 flex items-center gap-3">
        <div className="w-7 h-7 rounded-lg bg-danger/10 flex items-center justify-center shrink-0">
          <AlertTriangle size={13} className="text-danger" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-text">Something went wrong</p>
          <p className="text-xs text-dim mt-0.5">
            The agent encountered an error while processing your request.
          </p>
        </div>
        <button
          onClick={onRetry}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-danger/10 text-danger hover:bg-danger/15 border border-danger/20 transition-colors duration-100"
        >
          <RotateCcw size={10} />
          Retry
        </button>
      </div>
    </div>
  );
});
