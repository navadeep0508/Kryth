import React, { memo, useRef, useCallback } from "react";
import { ArrowUp, Square, Paperclip } from "lucide-react";
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
    el.style.height = Math.min(el.scrollHeight, 180) + "px";
  }, []);

  return (
    <div className="px-4 pb-4 pt-2 shrink-0">
      <div
        className={cn(
          "relative rounded-xl border transition-all duration-120",
          "bg-surface2",
          isRunning
            ? "border-[rgba(255,255,255,0.08)]"
            : "border-[rgba(255,255,255,0.08)] hover:border-[rgba(255,255,255,0.14)] focus-within:border-[rgba(255,255,255,0.18)]"
        )}
        style={{ boxShadow: "0 0 0 1px rgba(0,0,0,0.3), 0 4px 12px rgba(0,0,0,0.4)" }}
      >
        <textarea
          ref={ref}
          rows={1}
          disabled={isRunning}
          placeholder={isRunning ? "KRYTH is working…" : "Ask KRYTH anything…"}
          className={cn(
            "w-full bg-transparent text-sm text-text placeholder:text-subtle",
            "resize-none outline-none",
            "px-4 pt-3.5 pb-10",
            "min-h-[44px] max-h-[180px]",
            "leading-relaxed disabled:opacity-60",
          )}
          style={{ userSelect: "text" }}
          onKeyDown={onKeyDown}
          onInput={onInput}
        />

        {/* Bottom action bar */}
        <div className="absolute bottom-0 left-0 right-0 h-10 flex items-center px-3 gap-2">
          {/* Attach */}
          <button
            className="p-1 rounded text-subtle hover:text-muted transition-colors duration-100 disabled:opacity-30"
            disabled={isRunning}
            title="Attach file"
          >
            <Paperclip size={13} />
          </button>

          <div className="flex-1" />

          {/* Shift+Enter hint */}
          {!isRunning && (
            <span className="text-[10px] text-subtle/50 select-none">
              Shift+Enter for newline
            </span>
          )}

          {/* Send / Stop */}
          {isRunning ? (
            <button
              onClick={interrupt}
              className="flex items-center justify-center w-7 h-7 rounded-lg bg-surface3 border border-[rgba(255,255,255,0.08)] text-muted hover:text-text hover:border-[rgba(255,255,255,0.14)] transition-all duration-100"
              title="Stop"
            >
              <Square size={11} />
            </button>
          ) : (
            <button
              onClick={submit}
              className="flex items-center justify-center w-7 h-7 rounded-lg bg-[rgba(255,255,255,0.06)] hover:bg-[rgba(255,255,255,0.1)] text-muted hover:text-text transition-all duration-100 disabled:opacity-30"
              title="Send (Enter)"
            >
              <ArrowUp size={13} />
            </button>
          )}
        </div>
      </div>

      <p className="text-[10px] text-subtle/40 text-center mt-2 select-none">
        KRYTH can make mistakes. Review changes before applying.
      </p>
    </div>
  );
});
