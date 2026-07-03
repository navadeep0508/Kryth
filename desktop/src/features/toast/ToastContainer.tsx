import { memo } from "react";
import { X, CheckCircle2, AlertCircle, Info } from "lucide-react";
import { useToastStore, type Toast } from "@/store/toastStore";
import { cn } from "@/lib/utils";

export const ToastContainer = memo(function ToastContainer() {
  const toasts = useToastStore((s) => s.toasts);

  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-[9999] flex flex-col gap-2 pointer-events-none">
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} />
      ))}
    </div>
  );
});

const ToastItem = memo(function ToastItem({ toast }: { toast: Toast }) {
  const removeToast = useToastStore((s) => s.removeToast);

  return (
    <div
      className={cn(
        "pointer-events-auto flex items-center gap-2 px-3 py-2.5 rounded-lg border shadow-lg",
        "animate-slide-up min-w-[240px] max-w-[360px] group",
        toast.type === "success" && "bg-panel border-success/30",
        toast.type === "error" && "bg-panel border-danger/30",
        toast.type === "info" && "bg-panel border-accent/30"
      )}
    >
      <ToastIcon type={toast.type} />
      <span className="text-xs text-text flex-1">{toast.message}</span>
      <button
        onClick={() => removeToast(toast.id)}
        className="opacity-0 group-hover:opacity-100 transition-opacity text-dim hover:text-text shrink-0"
        aria-label="Dismiss notification"
      >
        <X size={12} />
      </button>
    </div>
  );
});

function ToastIcon({ type }: { type: Toast["type"] }) {
  switch (type) {
    case "success":
      return <CheckCircle2 size={14} className="text-success shrink-0" />;
    case "error":
      return <AlertCircle size={14} className="text-danger shrink-0" />;
    case "info":
      return <Info size={14} className="text-accent shrink-0" />;
  }
}
