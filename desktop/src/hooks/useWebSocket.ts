import { useEffect, useRef } from "react";
import { useUIStore } from "@/store/uiStore";

const WS_URL = "ws://127.0.0.1:7765/ws/events";

type MessageHandler = (data: string) => void;

// Module-level singleton: one connection shared across all hook instances
let _ws: WebSocket | null = null;
const _handlers = new Set<MessageHandler>();
let _retryDelay = 1000;
let _retryTimer: ReturnType<typeof setTimeout> | null = null;
let _manualClose = false;

function _connect() {
  if (_ws?.readyState === WebSocket.OPEN) return;

  _manualClose = false;
  _ws = new WebSocket(WS_URL);

  _ws.onopen = () => {
    _retryDelay = 1000;
    useUIStore.getState().setConnStatus("connected");
  };

  _ws.onmessage = (e) => {
    _handlers.forEach((fn) => fn(e.data as string));
  };

  _ws.onclose = () => {
    if (_manualClose) return;
    useUIStore.getState().setConnStatus("disconnected");
    // Exponential back-off: 1s → 2s → 4s → … → 30s
    _retryTimer = setTimeout(() => {
      _retryDelay = Math.min(_retryDelay * 2, 30_000);
      useUIStore.getState().setConnStatus("connecting");
      _connect();
    }, _retryDelay);
  };

  _ws.onerror = () => {
    _ws?.close();
  };
}

export function disconnectWebSocket() {
  _manualClose = true;
  if (_retryTimer) clearTimeout(_retryTimer);
  _ws?.close();
  _ws = null;
}

export function useWebSocket(onMessage: MessageHandler) {
  // Keep ref stable so add/remove don't trigger re-renders
  const ref = useRef(onMessage);
  ref.current = onMessage;

  useEffect(() => {
    const stable: MessageHandler = (data) => ref.current(data);
    _handlers.add(stable);
    _connect();
    return () => {
      _handlers.delete(stable);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
}
