import React, { useRef, useEffect, useCallback, memo } from "react";
import { VariableSizeList as List } from "react-window";
import { Bot, User } from "lucide-react";
import { cn } from "@/lib/utils";
import { useChatStore, type Message } from "@/store/chatStore";
import { StreamingMessage } from "./StreamingMessage";
import { ToolActionBubble } from "./ToolActionBubble";

const ESTIMATED_ROW_HEIGHT = 80;

export const MessageList = memo(function MessageList() {
  const messages = useChatStore((s) => s.messages);
  const listRef  = useRef<List>(null);
  const sizeMap  = useRef<Record<number, number>>({});
  const userScrolled = useRef(false);

  const getSize = useCallback((index: number) => sizeMap.current[index] ?? ESTIMATED_ROW_HEIGHT, []);

  const setSize = useCallback((index: number, size: number) => {
    if (sizeMap.current[index] === size) return;
    sizeMap.current[index] = size;
    listRef.current?.resetAfterIndex(index, false);
  }, []);

  // Auto-scroll to bottom when new messages arrive, unless user has scrolled up
  useEffect(() => {
    if (!userScrolled.current && messages.length > 0) {
      listRef.current?.scrollToItem(messages.length - 1, "end");
    }
  }, [messages.length, messages[messages.length - 1]?.content]);

  if (messages.length === 0) {
    return <EmptyState />;
  }

  return (
    <div className="flex-1 overflow-hidden">
      <AutoSizer>
        {({ height, width }) => (
          <List
            ref={listRef}
            height={height}
            width={width}
            itemCount={messages.length}
            itemSize={getSize}
            onScroll={({ scrollOffset, scrollUpdateWasRequested }) => {
              if (!scrollUpdateWasRequested) {
                const el = (listRef.current as unknown as { _outerRef: HTMLElement } | null)?._outerRef;
                if (!el) return;
                const atBottom = el.scrollHeight - scrollOffset - el.clientHeight < 80;
                userScrolled.current = !atBottom;
              }
            }}
          >
            {({ index, style }) => (
              <MessageRow
                key={messages[index].id}
                message={messages[index]}
                style={style}
                onHeight={(h) => setSize(index, h)}
              />
            )}
          </List>
        )}
      </AutoSizer>
    </div>
  );
});

const MessageRow = memo(function MessageRow({
  message, style, onHeight,
}: {
  message: Message;
  style: React.CSSProperties;
  onHeight: (h: number) => void;
}) {
  const rowRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!rowRef.current) return;
    const ro = new ResizeObserver(([entry]) => onHeight(entry.contentRect.height + 16));
    ro.observe(rowRef.current);
    return () => ro.disconnect();
  }, [onHeight]);

  const isUser = message.role === "user";

  return (
    <div style={style} className="px-4 pt-4">
      <div ref={rowRef} className={cn("flex gap-3", isUser && "flex-row-reverse")}>
        {/* Avatar */}
        <div
          className={cn(
            "w-7 h-7 rounded-full flex items-center justify-center shrink-0 mt-0.5",
            isUser ? "bg-accent text-bg" : "bg-surface2 text-muted border border-border"
          )}
        >
          {isUser ? <User size={13} /> : <Bot size={13} />}
        </div>

        {/* Content */}
        <div className={cn("flex flex-col gap-1.5 max-w-[85%]", isUser && "items-end")}>
          {/* Tool actions — shown above assistant message content */}
          {!isUser && message.toolActions && message.toolActions.length > 0 && (
            <div className="flex flex-col gap-1 w-full">
              {message.toolActions.map((a) => (
                <ToolActionBubble key={a.id} action={a} />
              ))}
            </div>
          )}

          {/* Bubble */}
          {message.content && (
            <div
              className={cn(
                "rounded-2xl px-3.5 py-2.5",
                isUser
                  ? "bg-accent/10 border border-accent/20 text-text"
                  : "bg-surface border border-border text-text"
              )}
            >
              {isUser ? (
                <p className="text-sm whitespace-pre-wrap">{message.content}</p>
              ) : (
                <StreamingMessage
                  content={message.content}
                  isStreaming={message.isStreaming}
                />
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
});

function EmptyState() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-4 p-8 text-center">
      <div className="w-12 h-12 rounded-2xl bg-accent/10 border border-accent/20 flex items-center justify-center">
        <Bot size={22} className="text-accent" />
      </div>
      <div>
        <h2 className="text-base font-semibold text-text mb-1">Ready</h2>
        <p className="text-sm text-muted max-w-xs">
          Ask KRYTH to build, debug, refactor, or explain your code.
        </p>
      </div>
    </div>
  );
}

// Minimal AutoSizer using ResizeObserver
function AutoSizer({
  children,
}: {
  children: (size: { width: number; height: number }) => React.ReactNode;
}) {
  const [size, setSize] = React.useState({ width: 0, height: 0 });
  const containerRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver(([entry]) => {
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  return (
    <div ref={containerRef} className="w-full h-full">
      {size.width > 0 && children(size)}
    </div>
  );
}
