import React, { memo, useRef, useCallback } from "react";
import { Send, Square } from "lucide-react";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chatStore";

export const MessageInput = memo(function MessageInput() {
  const ref = useRef<HTMLTextAreaElement>(null);
  const { status, sendUserMessage, interrupt } = useChatStore();
  const isRunning = status !== "idle";

  const submit = useCallback(() => {
    const text = ref.current?.value.trim();
    if (!text || isRunning) return;
    ref.current!.value = "";
    ref.current!.style.height = "auto";
    sendUserMessage(text);
  }, [isRunning, sendUserMessage]);

  const onKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }, [submit]);

  const onInput = useCallback((e: React.FormEvent<HTMLTextAreaElement>) => {
    const el = e.currentTarget;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, []);

  return (
    <div className="border-t border-border p-3 shrink-0">
      <div
        className={cn(
          "flex items-end gap-2 rounded-xl border bg-surface2 px-3 py-2",
          "transition-colors duration-120",
          isRunning ? "border-border" : "border-border hover:border-accent/30 focus-within:border-accent/50"
        )}
      >
        <textarea
          ref={ref}
          rows={1}
          disabled={isRunning}
          placeholder={isRunning ? "KRYTH is working…" : "Ask KRYTH anything… (Enter to send, Shift+Enter for newline)"}
          className="flex-1 bg-transparent text-sm text-text placeholder:text-muted resize-none outline-none min-h-[24px] max-h-[200px] py-0.5 leading-relaxed disabled:opacity-50"
          onKeyDown={onKeyDown}
          onInput={onInput}
        />
        {isRunning ? (
          <button
            onClick={interrupt}
            className="shrink-0 p-1.5 rounded-lg bg-error/10 text-error hover:bg-error/20 transition-colors duration-120"
            title="Stop"
          >
            <Square size={14} />
          </button>
        ) : (
          <button
            onClick={submit}
            className="shrink-0 p-1.5 rounded-lg bg-accent text-bg hover:bg-accent/90 transition-colors duration-120 disabled:opacity-50"
            title="Send (Enter)"
          >
            <Send size={14} />
          </button>
        )}
      </div>
      <p className="text-[10px] text-muted/50 text-center mt-1.5">
        KRYTH can make mistakes. Review changes before applying.
      </p>
    </div>
  );
});
