import { create } from "zustand";
import { bridge } from "@/lib/krythBridge";
import { useToastStore } from "@/store/toastStore";
import { useUIStore } from "@/store/uiStore";
import { useEditorStore } from "@/store/editorStore";
import { useProjectStore } from "@/store/projectStore";

// ── Context resolution: @mentions + active file ──────────────────────────────

interface AttachedFile {
  path: string;
  content: string;
}

function resolvePromptContext(rawContent: string): { resolvedContent: string; attachedFiles: AttachedFile[] } {
  const attachedFiles: AttachedFile[] = [];
  let resolvedContent = rawContent;

  // 1. Auto-attach active editor file (if user is looking at one)
  const editorState = useEditorStore.getState();
  const activeTab = editorState.tabs.find((t) => t.id === editorState.activeTabId);
  if (activeTab && activeTab.content) {
    attachedFiles.push({ path: activeTab.path, content: activeTab.content });
  }

  // 2. Resolve @filename mentions — replace with file content
  const mentionRegex = /@([\w./\\-]+\.\w+)/g;
  const mentions = [...rawContent.matchAll(mentionRegex)];
  if (mentions.length > 0) {
    const projectFiles = useProjectStore.getState().fileIndex;
    for (const match of mentions) {
      const mentioned = match[1];
      // Find matching file in project index
      const found = projectFiles.find((f) =>
        f.endsWith(mentioned) || f.includes(mentioned)
      );
      if (found && !attachedFiles.some((a) => a.path === found)) {
        // Mark for async fetch (content will be fetched by backend via path)
        resolvedContent = resolvedContent.replace(
          match[0],
          `[see attached: ${mentioned}]`
        );
        // We can't async fetch here, so tell backend to read it
        attachedFiles.push({ path: found, content: `[backend will read: ${found}]` });
      }
    }
  }

  return { resolvedContent, attachedFiles };
}

// ── Event types matching backend emissions ───────────────────────────────────

export type EventStatus = "queued" | "running" | "success" | "failed";

export interface PlanStep {
  id: string;
  label: string;
  status: "pending" | "active" | "done" | "failed";
}

export interface PlanEvent {
  type: "plan";
  id: string;
  steps: PlanStep[];
  collapsed: boolean;
}

export interface ToolCallEvent {
  type: "tool_call";
  id: string;
  tool: string;
  args: Record<string, unknown>;
  status: EventStatus;
  result?: string;
  runtime?: number;
  ts: number;
}

export interface AgentEvent {
  type: "agent_update";
  id: string;
  name: string;
  role: string;
  task: string;
  status: EventStatus;
  ts: number;
}

export interface DiffEvent {
  type: "diff";
  id: string;
  path: string;
  additions: number;
  deletions: number;
  hunks: DiffHunk[];
  status: "pending" | "applied" | "rejected";
}

export interface DiffHunk {
  header: string;
  lines: DiffLine[];
}

export interface DiffLine {
  type: "add" | "del" | "ctx";
  content: string;
  lineNum?: number;
}

export interface ApprovalEvent {
  type: "approval_required";
  id: string;
  message: string;
  tool?: string;
  risk: "low" | "medium" | "high";
  detail?: string;
  resolved?: boolean;
}

export interface TextEvent {
  type: "text";
  id: string;
  content: string;
  isStreaming: boolean;
}

export interface FinalResponseEvent {
  type: "final_response";
  id: string;
  summary: string;
  filesModified: string[];
  nextSteps: string[];
  commands?: string[];
}

export interface UserPromptEvent {
  type: "user_prompt";
  id: string;
  content: string;
  ts: number;
}

export interface ThinkingEvent {
  type: "thinking";
  id: string;
  label: string;
  isActive: boolean;
  ts: number;
}

export interface ShellEvent {
  type: "shell";
  id: string;
  command: string;
  cwd?: string;
  output: string;
  exitCode?: number;
  runtime?: number;
  isRunning: boolean;
}

export interface ReasoningEvent {
  type: "reasoning";
  id: string;
  content: string;
  isStreaming: boolean;
  collapsed: boolean;
}

export interface MissionAgent {
  id: string;
  name: string;
  task: string;
  status: "queued" | "running" | "done" | "failed";
  progress?: number;
}

export interface MissionEvent {
  type: "mission";
  id: string;
  title: string;
  agents: MissionAgent[];
  progress: number; // 0-1
  status: "running" | "done" | "failed";
  summary?: string;
  duration?: number;
  collapsed: boolean;
}

export interface ReflectionEvent {
  type: "reflection";
  id: string;
  insight: string;
  category: "failure_analysis" | "success_pattern" | "improvement";
}

export interface StageProgressEvent {
  type: "stage_progress";
  id: string;
  index: number;
  name: string;
  status: "running" | "done";
  detail?: string;
}

export type WorkspaceEvent =
  | UserPromptEvent
  | PlanEvent
  | ToolCallEvent
  | AgentEvent
  | DiffEvent
  | ApprovalEvent
  | TextEvent
  | FinalResponseEvent
  | ThinkingEvent
  | ShellEvent
  | ReasoningEvent
  | MissionEvent
  | ReflectionEvent
  | StageProgressEvent;

// ── Turn: groups events within a single agent turn ──────────────────────────

export interface Turn {
  id: string;
  events: WorkspaceEvent[];
  status: "active" | "done" | "error";
  ts: number;
  tokenUsage?: { prompt: number; completion: number; total: number };
}

// ── Store ───────────────────────────────────────────────────────────────────

export interface StageState {
  index: number;
  name: string;
  status: "pending" | "active" | "done" | "failed";
  detail?: string;
}

interface WorkspaceState {
  turns: Turn[];
  activeTurnId: string | null;
  cwd: string;
  pipelineStages: StageState[];

  // Actions
  sendPrompt: (content: string) => void;
  startTurn: () => string;
  pushEvent: (turnId: string, event: WorkspaceEvent) => void;
  updateEvent: (turnId: string, eventId: string, patch: Partial<WorkspaceEvent>) => void;
  endTurn: (turnId: string, status?: "done" | "error") => void;

  // Plan mutations
  updatePlanStep: (turnId: string, planId: string, stepId: string, status: PlanStep["status"]) => void;
  togglePlanCollapse: (turnId: string, planId: string) => void;

  // Streaming text
  appendText: (turnId: string, eventId: string, chunk: string) => void;
  endStreaming: (turnId: string, eventId: string) => void;

  // Shell streaming
  appendShellOutput: (turnId: string, eventId: string, chunk: string) => void;
  endShell: (turnId: string, eventId: string, exitCode: number, runtime?: number) => void;

  // Reasoning streaming
  appendReasoning: (turnId: string, eventId: string, chunk: string) => void;
  endReasoning: (turnId: string, eventId: string) => void;
  toggleReasoningCollapse: (turnId: string, eventId: string) => void;

  // Token usage
  updateTurnTokens: (turnId: string, usage: { prompt: number; completion: number; total: number }) => void;

  // Tool updates
  completeToolCall: (turnId: string, toolId: string, result: string, runtime?: number) => void;
  failToolCall: (turnId: string, toolId: string, result: string) => void;

  // Diff actions
  applyDiff: (turnId: string, diffId: string) => void;
  rejectDiff: (turnId: string, diffId: string) => void;

  // Approval actions
  resolveApproval: (turnId: string, approvalId: string, approved: boolean) => void;

  // Mission actions
  updateMissionProgress: (turnId: string, missionId: string, agentId: string, status: MissionAgent["status"], progress: number) => void;
  completeMission: (turnId: string, missionId: string, success: boolean, summary: string, duration?: number) => void;
  toggleMissionCollapse: (turnId: string, missionId: string) => void;

  // Retry
  retryLast: () => void;

  updatePipelineStage: (index: number, name: string, status: "pending" | "active" | "done" | "failed", detail?: string) => void;
  resetPipeline: () => void;

  setCwd: (cwd: string) => void;
  clearAll: () => void;
  interrupt: () => void;
}

let _turnId = 0;
let _eventId = 0;
function nextTurnId() { return `turn-${++_turnId}-${Date.now()}`; }
function nextEventId() { return `evt-${++_eventId}-${Date.now()}`; }

export { nextEventId };

const STAGE_NAMES = ["Task", "Scan", "Plan", "Execute", "Verify", "Done"];

function resetPipelineStages(): StageState[] {
  return STAGE_NAMES.map((name, i) => ({
    index: i,
    name,
    status: "pending" as const,
    detail: undefined,
  }));
}

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  turns: [],
  activeTurnId: null,
  cwd: "",
  pipelineStages: resetPipelineStages(),

  sendPrompt: (content) => {
    const turnId = get().startTurn();
    const evtId = nextEventId();

    // Resolve @mentions and attach active file context
    const { resolvedContent, attachedFiles } = resolvePromptContext(content);

    get().pushEvent(turnId, {
      type: "user_prompt",
      id: evtId,
      content: resolvedContent,
      ts: Date.now(),
    });
    get().pushEvent(turnId, {
      type: "thinking",
      id: nextEventId(),
      label: "Thinking…",
      isActive: true,
      ts: Date.now(),
    });
    const { cwd } = get();
    const ui = useUIStore.getState();
    ui.setAgentStatus("thinking");

    // Build enriched prompt with file context
    let enrichedPrompt = resolvedContent;
    if (attachedFiles.length > 0) {
      const contextBlock = attachedFiles
        .map((f) => `[File: ${f.path}]\n${f.content}`)
        .join("\n\n");
      enrichedPrompt = `${resolvedContent}\n\n---\nAttached files:\n${contextBlock}`;
    }

    // Use workspace cwd — never fall back to "." which resolves to the server's cwd
    bridge.runAgent(enrichedPrompt, cwd || "").catch((err) => {
      // Deactivate thinking indicator
      const currentTurn = get().turns.find((t) => t.id === turnId);
      const thinkingEvts = currentTurn?.events.filter((e) => e.type === "thinking" && (e as any).isActive) ?? [];
      for (const evt of thinkingEvts) {
        get().updateEvent(turnId, evt.id, { isActive: false });
      }
      ui.setAgentStatus("error");
      get().endTurn(turnId, "error");
      const msg = err instanceof Error ? err.message : "Agent run failed";
      const isNoFolder = msg.includes("No project folder");
      get().pushEvent(turnId, {
        type: "text",
        id: nextEventId(),
        content: isNoFolder
          ? `**Open a folder first.** Use the Files panel in the sidebar to open a project folder before running commands.`
          : `Failed to connect to KRYTH backend.\n\nStart the server with:\n\`\`\`\npython -m kryth.desktop_main\n\`\`\`\n\nError: ${msg}`,
        isStreaming: false,
      });
      useToastStore.getState().addToast(isNoFolder ? "Open a folder first" : msg, "error");
    });
  },

  startTurn: () => {
    const id = nextTurnId();
    set((s) => ({
      turns: [...s.turns, { id, events: [], status: "active", ts: Date.now() }],
      activeTurnId: id,
    }));
    return id;
  },

  pushEvent: (turnId, event) => {
    set((s) => ({
      turns: s.turns.map((t) =>
        t.id === turnId ? { ...t, events: [...t.events, event] } : t
      ),
    }));
  },

  updateEvent: (turnId, eventId, patch) => {
    set((s) => ({
      turns: s.turns.map((t) =>
        t.id === turnId
          ? {
              ...t,
              events: t.events.map((e) =>
                e.id === eventId ? ({ ...e, ...patch } as WorkspaceEvent) : e
              ),
            }
          : t
      ),
    }));
  },

  endTurn: (turnId, status = "done") => {
    set((s) => ({
      turns: s.turns.map((t) =>
        t.id === turnId
          ? {
              ...t,
              status,
              events: t.events.map((e) =>
                e.type === "thinking" && (e as ThinkingEvent).isActive
                  ? { ...e, isActive: false }
                  : e
              ),
            }
          : t
      ),
      activeTurnId: s.activeTurnId === turnId ? null : s.activeTurnId,
    }));
  },

  updatePlanStep: (turnId, planId, stepId, status) => {
    set((s) => ({
      turns: s.turns.map((t) =>
        t.id === turnId
          ? {
              ...t,
              events: t.events.map((e) =>
                e.id === planId && e.type === "plan"
                  ? {
                      ...e,
                      steps: (e as PlanEvent).steps.map((st) =>
                        st.id === stepId ? { ...st, status } : st
                      ),
                    }
                  : e
              ),
            }
          : t
      ),
    }));
  },

  togglePlanCollapse: (turnId, planId) => {
    set((s) => ({
      turns: s.turns.map((t) =>
        t.id === turnId
          ? {
              ...t,
              events: t.events.map((e) =>
                e.id === planId && e.type === "plan"
                  ? { ...e, collapsed: !(e as PlanEvent).collapsed }
                  : e
              ),
            }
          : t
      ),
    }));
  },

  appendText: (turnId, eventId, chunk) => {
    set((s) => ({
      turns: s.turns.map((t) =>
        t.id === turnId
          ? {
              ...t,
              events: t.events.map((e) =>
                e.id === eventId && e.type === "text"
                  ? { ...e, content: (e as TextEvent).content + chunk }
                  : e
              ),
            }
          : t
      ),
    }));
  },

  endStreaming: (turnId, eventId) => {
    set((s) => ({
      turns: s.turns.map((t) =>
        t.id === turnId
          ? {
              ...t,
              events: t.events.map((e) =>
                e.id === eventId && e.type === "text"
                  ? { ...e, isStreaming: false }
                  : e
              ),
            }
          : t
      ),
    }));
  },

  appendShellOutput: (turnId, eventId, chunk) => {
    set((s) => ({
      turns: s.turns.map((t) =>
        t.id === turnId
          ? {
              ...t,
              events: t.events.map((e) =>
                e.id === eventId && e.type === "shell"
                  ? { ...e, output: (e as ShellEvent).output + chunk }
                  : e
              ),
            }
          : t
      ),
    }));
  },

  endShell: (turnId, eventId, exitCode, runtime) => {
    set((s) => ({
      turns: s.turns.map((t) =>
        t.id === turnId
          ? {
              ...t,
              events: t.events.map((e) =>
                e.id === eventId && e.type === "shell"
                  ? { ...e, isRunning: false, exitCode, runtime }
                  : e
              ),
            }
          : t
      ),
    }));
  },

  appendReasoning: (turnId, eventId, chunk) => {
    set((s) => ({
      turns: s.turns.map((t) =>
        t.id === turnId
          ? {
              ...t,
              events: t.events.map((e) =>
                e.id === eventId && e.type === "reasoning"
                  ? { ...e, content: (e as ReasoningEvent).content + chunk }
                  : e
              ),
            }
          : t
      ),
    }));
  },

  endReasoning: (turnId, eventId) => {
    set((s) => ({
      turns: s.turns.map((t) =>
        t.id === turnId
          ? {
              ...t,
              events: t.events.map((e) =>
                e.id === eventId && e.type === "reasoning"
                  ? { ...e, isStreaming: false, collapsed: true }
                  : e
              ),
            }
          : t
      ),
    }));
  },

  toggleReasoningCollapse: (turnId, eventId) => {
    set((s) => ({
      turns: s.turns.map((t) =>
        t.id === turnId
          ? {
              ...t,
              events: t.events.map((e) =>
                e.id === eventId && e.type === "reasoning"
                  ? { ...e, collapsed: !(e as ReasoningEvent).collapsed }
                  : e
              ),
            }
          : t
      ),
    }));
  },

  updateTurnTokens: (turnId, usage) => {
    set((s) => ({
      turns: s.turns.map((t) =>
        t.id === turnId
          ? {
              ...t,
              tokenUsage: t.tokenUsage
                ? {
                    prompt: t.tokenUsage.prompt + usage.prompt,
                    completion: t.tokenUsage.completion + usage.completion,
                    total: t.tokenUsage.total + usage.total,
                  }
                : { ...usage },
            }
          : t
      ),
    }));
  },

  completeToolCall: (turnId, toolId, result, runtime) => {
    set((s) => ({
      turns: s.turns.map((t) =>
        t.id === turnId
          ? {
              ...t,
              events: t.events.map((e) =>
                e.id === toolId && e.type === "tool_call"
                  ? { ...e, status: "success" as const, result, runtime }
                  : e
              ),
            }
          : t
      ),
    }));
  },

  failToolCall: (turnId, toolId, result) => {
    set((s) => ({
      turns: s.turns.map((t) =>
        t.id === turnId
          ? {
              ...t,
              events: t.events.map((e) =>
                e.id === toolId && e.type === "tool_call"
                  ? { ...e, status: "failed" as const, result }
                  : e
              ),
            }
          : t
      ),
    }));
  },

  applyDiff: (turnId, diffId) => {
    set((s) => ({
      turns: s.turns.map((t) =>
        t.id === turnId
          ? {
              ...t,
              events: t.events.map((e) =>
                e.id === diffId && e.type === "diff"
                  ? { ...e, status: "applied" as const }
                  : e
              ),
            }
          : t
      ),
    }));
  },

  rejectDiff: (turnId, diffId) => {
    set((s) => ({
      turns: s.turns.map((t) =>
        t.id === turnId
          ? {
              ...t,
              events: t.events.map((e) =>
                e.id === diffId && e.type === "diff"
                  ? { ...e, status: "rejected" as const }
                  : e
              ),
            }
          : t
      ),
    }));
  },

  resolveApproval: (turnId, approvalId, approved) => {
    // Approvals are handled locally — no external call needed
    set((s) => ({
      turns: s.turns.map((t) =>
        t.id === turnId
          ? {
              ...t,
              events: t.events.map((e) =>
                e.id === approvalId && e.type === "approval_required"
                  ? { ...e, resolved: true }
                  : e
              ),
            }
          : t
      ),
    }));
  },

  updateMissionProgress: (turnId, missionId, agentId, status, progress) => {
    set((s) => ({
      turns: s.turns.map((t) =>
        t.id === turnId
          ? {
              ...t,
              events: t.events.map((e) => {
                if (e.id !== missionId || e.type !== "mission") return e;
                const mission = e as MissionEvent;
                const agents = mission.agents.map((a) =>
                  a.id === agentId ? { ...a, status, progress } : a
                );
                const doneCount = agents.filter((a) => a.status === "done" || a.status === "failed").length;
                const overallProgress = agents.length > 0 ? doneCount / agents.length : progress;
                return { ...mission, agents, progress: Math.max(mission.progress, overallProgress) };
              }),
            }
          : t
      ),
    }));
  },

  completeMission: (turnId, missionId, success, summary, duration) => {
    set((s) => ({
      turns: s.turns.map((t) =>
        t.id === turnId
          ? {
              ...t,
              events: t.events.map((e) =>
                e.id === missionId && e.type === "mission"
                  ? {
                      ...e,
                      status: success ? "done" as const : "failed" as const,
                      progress: 1,
                      summary,
                      duration,
                      collapsed: true,
                    }
                  : e
              ),
            }
          : t
      ),
    }));
  },

  toggleMissionCollapse: (turnId, missionId) => {
    set((s) => ({
      turns: s.turns.map((t) =>
        t.id === turnId
          ? {
              ...t,
              events: t.events.map((e) =>
                e.id === missionId && e.type === "mission"
                  ? { ...e, collapsed: !(e as MissionEvent).collapsed }
                  : e
              ),
            }
          : t
      ),
    }));
  },

  retryLast: () => {
    const { turns } = get();
    // Search backwards through turns for the last user_prompt event
    for (let i = turns.length - 1; i >= 0; i--) {
      const turn = turns[i];
      for (let j = turn.events.length - 1; j >= 0; j--) {
        const evt = turn.events[j];
        if (evt.type === "user_prompt") {
          get().sendPrompt((evt as UserPromptEvent).content);
          return;
        }
      }
    }
  },

  updatePipelineStage: (index, name, status, detail) => {
    set((s) => ({
      pipelineStages: s.pipelineStages.map((st) =>
        st.index === index
          ? { ...st, name, status, detail }
          : // Set earlier stages to "done" if this one just went active
          status === "active" && st.index < index && st.status === "pending"
            ? { ...st, status: "done" as const }
            : st
      ),
    }));
  },

  resetPipeline: () => set({ pipelineStages: resetPipelineStages() }),

  setCwd: (cwd) => set({ cwd }),
  clearAll: () => set({ turns: [], activeTurnId: null, pipelineStages: resetPipelineStages() }),
  interrupt: () => {
    bridge.stopAgent().catch(() => {});
    const { activeTurnId } = get();
    if (activeTurnId) {
      // Push a friendly interrupted message
      get().pushEvent(activeTurnId, {
        type: "text",
        id: nextEventId(),
        content: "Interrupted. What would you like to do instead?",
        isStreaming: false,
      });
      get().endTurn(activeTurnId, "done");
    }
    useUIStore.getState().setAgentStatus("idle");
  },
}));
