import { memo } from "react";
import { User } from "lucide-react";
import type { UserPromptEvent } from "@/store/workspaceStore";

export const UserPromptCard = memo(function UserPromptCard({ event }: { event: UserPromptEvent }) {
  return (
    <div className="flex items-start gap-3">
      <div className="w-6 h-6 rounded-full bg-text/5 border border-border-soft flex items-center justify-center shrink-0 mt-0.5">
        <User size={12} className="text-muted" />
      </div>
      <p className="text-[13px] text-text leading-relaxed pt-0.5 whitespace-pre-wrap break-words">
        {event.content}
      </p>
    </div>
  );
});
