import { memo } from "react";
import { MessageList } from "./MessageList";
import { MessageInput } from "./MessageInput";

export default memo(function ChatPanel() {
  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      <MessageList />
      <MessageInput />
    </div>
  );
});
