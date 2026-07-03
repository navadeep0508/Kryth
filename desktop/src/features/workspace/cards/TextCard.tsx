import { memo, useCallback, useState, useMemo, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { Copy, Check, Bot } from "lucide-react";
import type { TextEvent } from "@/store/workspaceStore";

export const TextCard = memo(function TextCard({ event }: { event: TextEvent }) {
  // Don't render empty content (can happen from filtered chunks)
  if (!event.content && !event.isStreaming) return null;

  return (
    <div className="flex items-start gap-3 py-1.5">
      {/* Avatar */}
      <div className="w-6 h-6 rounded-full bg-gradient-to-br from-accent/15 to-accent/5 border border-accent/20 flex items-center justify-center shrink-0 mt-0.5">
        <Bot size={12} className="text-accent" />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0 pt-0.5">
        <div className="prose-kryth">
          <MemoizedMarkdown content={event.content} />
          {event.isStreaming && <StreamingCursor />}
        </div>
      </div>
    </div>
  );
});

/* ── Memoized markdown to avoid re-parsing unchanged content ───── */
const MemoizedMarkdown = memo(function MemoizedMarkdown({ content }: { content: string }) {
  if (!content) return null;

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeHighlight]}
      components={{
        pre({ children }) {
          return <CodeBlockWrapper>{children}</CodeBlockWrapper>;
        },
        // Better inline code
        code({ children, className, ...props }) {
          const isBlock = className?.includes("hljs") || className?.includes("language-");
          if (isBlock) {
            return <code className={className} {...props}>{children}</code>;
          }
          return <code className="inline-code" {...props}>{children}</code>;
        },
        // Better links
        a({ href, children }) {
          return (
            <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
              {children}
            </a>
          );
        },
      }}
    >
      {content}
    </ReactMarkdown>
  );
});

/* ── Streaming cursor ──────────────────────────────────────────── */
function StreamingCursor() {
  return (
    <span className="inline-flex items-center ml-0.5 align-middle">
      <span className="w-[3px] h-[14px] bg-accent rounded-[1px] animate-cursor-blink" />
    </span>
  );
}

/* ── Code block with header bar and copy button ───────────────── */
function CodeBlockWrapper({ children }: { children: ReactNode }) {
  const [copied, setCopied] = useState(false);

  // Extract language and text from the code element
  const { language, codeText } = useMemo(() => {
    let lang = "";
    let text = "";
    if (children && typeof children === "object" && "props" in (children as any)) {
      const codeProps = (children as any).props;
      const className: string = codeProps?.className ?? "";
      const match = className.match(/language-(\w+)/);
      if (match) lang = match[1];
      text = extractText(codeProps?.children);
    }
    return { language: lang, codeText: text };
  }, [children]);

  const handleCopy = useCallback(() => {
    if (!codeText) return;
    navigator.clipboard.writeText(codeText).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [codeText]);

  return (
    <div className="code-block-wrapper group/code">
      <div className="code-block-header">
        <span className="text-[10px] font-mono text-dim uppercase tracking-wider">
          {language || "code"}
        </span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] text-dim hover:text-text hover:bg-panel-hover transition-all duration-100"
          aria-label="Copy code"
        >
          {copied ? (
            <>
              <Check size={10} className="text-success" />
              <span className="text-success">Copied</span>
            </>
          ) : (
            <>
              <Copy size={10} />
              <span className="opacity-0 group-hover/code:opacity-100 transition-opacity duration-150">Copy</span>
            </>
          )}
        </button>
      </div>
      <pre className="!mt-0 !rounded-t-none !border-t-0">{children}</pre>
    </div>
  );
}

/* ── Recursively extract text from React children ─────────────── */
function extractText(node: unknown): string {
  if (typeof node === "string") return node;
  if (typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(extractText).join("");
  if (node && typeof node === "object" && "props" in (node as any)) {
    return extractText((node as any).props?.children);
  }
  return "";
}
