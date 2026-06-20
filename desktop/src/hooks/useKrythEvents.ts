import { useWebSocket } from "./useWebSocket";
import { dispatchEvent, type KrythEvent } from "@/lib/eventMapper";

export function useKrythEvents() {
  useWebSocket((raw) => {
    try {
      const event = JSON.parse(raw) as KrythEvent;
      dispatchEvent(event);
    } catch {
      // Ignore parse errors from malformed frames
    }
  });
}
