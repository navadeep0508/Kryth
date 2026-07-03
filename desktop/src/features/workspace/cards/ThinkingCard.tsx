import { memo, useEffect, useState } from "react";
import type { ThinkingEvent } from "@/store/workspaceStore";

const THINKING_TIMEOUT_MS = 45_000;

export const ThinkingCard = memo(function ThinkingCard({ event }: { event: ThinkingEvent }) {
  const [timedOut, setTimedOut] = useState(false);

  useEffect(() => {
    if (!event.isActive) return;
    setTimedOut(false);
    const timer = setTimeout(() => setTimedOut(true), THINKING_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [event.isActive]);

  if (!event.isActive || timedOut) return null;

  return (
    <div className="flex items-center gap-3 px-3 py-2">
      <ThinkingAnimation />
      <span className="text-xs text-muted">{event.label}</span>
    </div>
  );
});

function ThinkingAnimation() {
  return (
    <div className="flex items-center gap-[3px]">
      <span className="w-[5px] h-[5px] rounded-full bg-accent/70 animate-thinking-1" />
      <span className="w-[5px] h-[5px] rounded-full bg-accent/70 animate-thinking-2" />
      <span className="w-[5px] h-[5px] rounded-full bg-accent/70 animate-thinking-3" />
    </div>
  );
}
