import React, { memo } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import { cn } from "@/lib/utils";

interface Props {
  content: string;
  isStreaming?: boolean;
}

export const StreamingMessage = memo(function StreamingMessage({ content, isStreaming }: Props) {
  return (
    <div
      className={cn(
        "prose-kryth",
        isStreaming && "after:content-['▋'] after:ml-0.5 after:text-muted after:animate-cursor-blink"
      )}
    >
      <ReactMarkdown
        rehypePlugins={[rehypeHighlight]}
        components={{
          pre: ({ children }) => (
            <pre className="bg-[#0D0D0D] border border-[rgba(255,255,255,0.06)] rounded-lg p-3 overflow-x-auto my-3">
              {children}
            </pre>
          ),
          code: (({ className, children }: { className?: string; children?: React.ReactNode }) => {
            const isInline = !className;
            return isInline ? (
              <code className="px-1.5 py-0.5 rounded bg-[rgba(255,255,255,0.06)] border border-[rgba(255,255,255,0.06)] text-[#A1A1AA] font-mono text-[11px]">
                {children}
              </code>
            ) : (
              <code className={cn(className, "font-mono text-xs")}>{children}</code>
            );
          }) as React.ComponentType,
          p: ({ children }) => <p className="mb-2.5 last:mb-0 leading-relaxed text-text/90">{children}</p>,
          ul: ({ children }) => <ul className="list-disc list-inside mb-2.5 space-y-1">{children}</ul>,
          ol: ({ children }) => <ol className="list-decimal list-inside mb-2.5 space-y-1">{children}</ol>,
          li: ({ children }) => <li className="text-sm text-text/80">{children}</li>,
          a: ({ href, children }) => (
            <a href={href} className="text-text/60 underline underline-offset-2 hover:text-text transition-colors" target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
          blockquote: ({ children }) => (
            <blockquote className="border-l border-[rgba(255,255,255,0.1)] pl-3 text-muted my-2">
              {children}
            </blockquote>
          ),
          h1: ({ children }) => <h1 className="text-base font-semibold mb-2 mt-4 text-text">{children}</h1>,
          h2: ({ children }) => <h2 className="text-sm font-semibold mb-1.5 mt-3 text-text">{children}</h2>,
          h3: ({ children }) => <h3 className="text-sm font-medium mb-1 mt-2 text-text/80">{children}</h3>,
          strong: ({ children }) => <strong className="font-semibold text-text">{children}</strong>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});
