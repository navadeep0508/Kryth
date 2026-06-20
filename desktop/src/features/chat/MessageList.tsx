import React, { useRef, useEffect, useCallback, memo } from "react";
import { VariableSizeList as List } from "react-window";
import { Zap } from "lucide-react";
import { cn } from "@/lib/utils";
import { useChatStore, type Message } from "@/store/chatStore";
import { StreamingMessage } from "./StreamingMessage";
import { ToolActionBubble } from "./ToolActionBubble";

const ESTIMATED_ROW_HEIGHT = 72;

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
    const ro = new ResizeObserver(([entry]) => onHeight(entry.contentRect.height + 12));
    ro.observe(rowRef.current);
    return () => ro.disconnect();
  }, [onHeight]);

  const isUser = message.role === "user";

  return (
    <div style={style} className="px-6 pt-3">
      <div ref={rowRef}>
        {isUser ? (
          /* User message — right-aligned, subtle pill */
          <div className="flex justify-end">
            <div className="max-w-[75%] px-3.5 py-2.5 rounded-xl bg-surface2 border border-[rgba(255,255,255,0.06)]">
              <p className="text-sm text-text whitespace-pre-wrap leading-relaxed">{message.content}</p>
            </div>
          </div>
        ) : (
          /* Assistant message — left-aligned, no bubble */
          <div className="flex flex-col gap-1.5 max-w-[90%]">
            {/* Tool actions */}
            {message.toolActions && message.toolActions.length > 0 && (
              <div className="flex flex-col gap-1 mb-1">
                {message.toolActions.map((a) => (
                  <ToolActionBubble key={a.id} action={a} />
                ))}
              </div>
            )}
            {/* Content */}
            {message.content && (
              <StreamingMessage content={message.content} isStreaming={message.isStreaming} />
            )}
          </div>
        )}
      </div>
    </div>
  );
});

function EmptyState() {
  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-3 p-8 text-center">
      <div className="w-10 h-10 rounded-xl bg-surface2 border border-[rgba(255,255,255,0.06)] flex items-center justify-center">
        <Zap size={18} className="text-accent" />
      </div>
      <div>
        <h2 className="text-sm font-semibold text-text mb-1">KRYTH</h2>
        <p className="text-xs text-subtle max-w-xs leading-relaxed">
          Build, debug, refactor, or explain — just ask.
        </p>
      </div>
    </div>
  );
}

function AutoSizer({ children }: { children: (size: { width: number; height: number }) => React.ReactNode }) {
  const [size, setSize] = React.useState({ width: 0, height: 0 });
  const ref = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([entry]) =>
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height })
    );
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);

  return (
    <div ref={ref} className="w-full h-full">
      {size.width > 0 && children(size)}
    </div>
  );
}
