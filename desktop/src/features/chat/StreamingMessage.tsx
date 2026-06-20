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
    <div className={cn("prose-kryth text-sm", isStreaming && "after:content-['▋'] after:ml-0.5 after:text-accent after:animate-pulse-soft")}>
      <ReactMarkdown
        rehypePlugins={[rehypeHighlight]}
        components={{
          pre: ({ children }) => (
            <pre className="bg-bg border border-border rounded-lg p-3 overflow-x-auto text-xs my-2">
              {children}
            </pre>
          ),
          code: (({ className, children }: { className?: string; children?: React.ReactNode }) => {
            const isInline = !className;
            return isInline ? (
              <code className="px-1 py-0.5 rounded bg-surface2 text-accent font-mono text-[11px]">
                {children}
              </code>
            ) : (
              <code className={className}>
                {children}
              </code>
            );
          }) as React.ComponentType,
          p: ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
          ul: ({ children }) => <ul className="list-disc list-inside mb-2 space-y-0.5">{children}</ul>,
          ol: ({ children }) => <ol className="list-decimal list-inside mb-2 space-y-0.5">{children}</ol>,
          li: ({ children }) => <li className="text-sm">{children}</li>,
          a: ({ href, children }) => (
            <a href={href} className="text-accent underline" target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-accent/40 pl-3 text-muted italic my-2">
              {children}
            </blockquote>
          ),
          h1: ({ children }) => <h1 className="text-base font-semibold mb-2 mt-3">{children}</h1>,
          h2: ({ children }) => <h2 className="text-sm font-semibold mb-1.5 mt-3">{children}</h2>,
          h3: ({ children }) => <h3 className="text-sm font-medium mb-1 mt-2">{children}</h3>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});
