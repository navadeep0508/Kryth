import { useEffect, useSyncExternalStore } from "react";
import { RuntimeStore, getStore } from "../../store/runtimeStore";
import type { StoreEvent } from "../../store/runtimeStore";

function useStoreEvent(event: StoreEvent): RuntimeStore {
  const store = getStore();
  useSyncExternalStore(
    (onChange) => store.on(event, onChange),
    () => store,
  );
  return store;
}

export function useStore(): RuntimeStore {
  return getStore();
}

export function usePipeline() {
  const store = useStoreEvent("pipeline:stage");
  return store.pipeline;
}

export function useSession() {
  const store = useStoreEvent("session:update");
  return store.session;
}

export function useActiveTools() {
  const store = useStoreEvent("tool:start");
  return store.activeTools;
}

export function useToolHistory() {
  const store = useStoreEvent("tool:end");
  return store.toolHistory;
}

export function useToolSummaries() {
  const store = useStoreEvent("tool:summary");
  return store.toolSummaries;
}

export function useUI() {
  const store = useStoreEvent("ui:layout");
  return store.ui;
}

export function useBuffer() {
  const store = useStoreEvent("buffer:write");
  return store.buffer;
}

export function useViewport() {
  const store = useStoreEvent("viewport:scroll");
  return store.viewport;
}
