import { create } from "zustand";
import { bridge } from "@/lib/krythBridge";

export type AgentStatus = "idle" | "thinking" | "running";

export interface ToolAction {
  id: string;
  label: string;
  status: "running" | "done" | "failed";
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolActions: ToolAction[];
  isStreaming: boolean;
  ts: number;
}

interface ChatState {
  messages: Message[];
  status: AgentStatus;
  cwd: string;

  // Actions
  sendUserMessage: (content: string) => string;
  beginStreaming: () => void;
  appendChunk: (piece: string) => void;
  endStreaming: () => void;
  addToolAction: (action: ToolAction) => void;
  markToolDone: (id: string) => void;
  markToolFailed: (id: string) => void;
  finalizeTurn: () => void;
  setStatus: (s: AgentStatus) => void;
  setCwd: (cwd: string) => void;
  clearMessages: () => void;
  interrupt: () => void;
}

let _msgId = 0;
function nextId() { return `msg-${++_msgId}-${Date.now()}`; }

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  status: "idle",
  cwd: "",

  sendUserMessage: (content) => {
    const id = nextId();
    set((s) => ({
      status: "thinking",
      messages: [
        ...s.messages,
        { id, role: "user", content, toolActions: [], isStreaming: false, ts: Date.now() },
      ],
    }));
    const { cwd } = get();
    bridge.runAgent(content, cwd).catch(() => get().finalizeTurn());
    return id;
  },

  beginStreaming: () => {
    const id = nextId();
    set((s) => ({
      status: "running",
      messages: [
        ...s.messages,
        { id, role: "assistant", content: "", toolActions: [], isStreaming:true, ts: Date.now() },
      ],
    }));
  },

  appendChunk: (piece) => {
    set((s) => {
      const msgs = [...s.messages];
      const last = msgs[msgs.length - 1];
      if (last?.role === "assistant" && last.isStreaming) {
        msgs[msgs.length - 1] = { ...last, content: last.content + piece };
      }
      return { messages: msgs };
    });
  },

  endStreaming: () => {
    set((s) => {
      const msgs = [...s.messages];
      const last = msgs[msgs.length - 1];
      if (last?.role === "assistant") {
        msgs[msgs.length - 1] = { ...last, isStreaming:false };
      }
      return { messages: msgs };
    });
  },

  addToolAction: (action) => {
    set((s) => {
      const msgs = [...s.messages];
      const last = msgs[msgs.length - 1];
      if (last?.role === "assistant") {
        msgs[msgs.length - 1] = {
          ...last,
          toolActions: [...last.toolActions, action],
        };
      }
      return { messages: msgs };
    });
  },

  markToolDone: (id) => {
    set((s) => ({
      messages: s.messages.map((m) =>
        m.role === "assistant"
          ? {
              ...m,
              toolActions: m.toolActions.map((t) =>
                t.id === id ? { ...t, status: "done" as const } : t
              ),
            }
          : m
      ),
    }));
  },

  markToolFailed: (id) => {
    set((s) => ({
      messages: s.messages.map((m) =>
        m.role === "assistant"
          ? {
              ...m,
              toolActions: m.toolActions.map((t) =>
                t.id === id ? { ...t, status: "failed" as const } : t
              ),
            }
          : m
      ),
    }));
  },

  finalizeTurn: () => {
    set((s) => {
      const msgs = [...s.messages];
      const last = msgs[msgs.length - 1];
      if (last?.role === "assistant") {
        msgs[msgs.length - 1] = { ...last, isStreaming:false };
      }
      return { messages: msgs, status: "idle" };
    });
  },

  setStatus: (status) => set({ status }),
  setCwd: (cwd) => set({ cwd }),
  clearMessages: () => set({ messages: [], status: "idle" }),
  interrupt: () => {
    bridge.stopAgent().catch(() => {});
    set({ status: "idle" });
  },
}));
