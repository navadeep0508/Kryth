import { useEffect, useRef } from "react";
import { bridge } from "@/lib/krythBridge";
import { useUIStore } from "@/store/uiStore";
import { useToastStore } from "@/store/toastStore";
import { useWorkspaceStore, nextEventId } from "@/store/workspaceStore";

// Module-level singleton to prevent multiple WebSocket connections
// (React StrictMode mounts/unmounts/remounts in dev)
let _wsInstance: WebSocket | null = null;
let _wsConnected = false;
let _reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let _backoff = 1000;
let _hasConnectedOnce = false;
let _disposed = false;

function connectSingleton() {
  if (_wsInstance && (_wsInstance.readyState === WebSocket.OPEN || _wsInstance.readyState === WebSocket.CONNECTING)) {
    return; // Already connected or connecting
  }

  _disposed = false;

  try {
    const ws = new WebSocket(bridge.wsUrl);
    _wsInstance = ws;

    ws.onopen = () => {
      _backoff = 1000;
      _wsConnected = true;
      useUIStore.getState().setConnStatus("connected");
      if (_hasConnectedOnce) {
        useToastStore.getState().addToast("Backend connected", "success");
      }
      _hasConnectedOnce = true;
    };

    ws.onclose = () => {
      _wsConnected = false;
      _wsInstance = null;
      if (_disposed) return; // Don't reconnect if intentionally disposed
      useUIStore.getState().setConnStatus("disconnected");
      _reconnectTimer = setTimeout(() => {
        _backoff = Math.min(_backoff * 2, 30000);
        if (!_disposed) connectSingleton();
      }, _backoff);
    };

    ws.onerror = () => ws.close();

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        handleEvent(msg);
      } catch {
        // ignore malformed messages
      }
    };
  } catch {
    useUIStore.getState().setConnStatus("disconnected");
    _reconnectTimer = setTimeout(() => {
      _backoff = Math.min(_backoff * 2, 30000);
      if (!_disposed) connectSingleton();
    }, _backoff);
  }
}

export function useKrythEvents() {
  useEffect(() => {
    connectSingleton();

    return () => {
      // In StrictMode this runs on first unmount, but we DON'T close
      // the WebSocket — we keep the singleton alive. Only truly dispose
      // if the app is unmounting for real (which doesn't happen in SPA).
    };
  }, []);
}

interface RawEvent {
  kind: string;
  id?: string;
  ts?: number;
  data?: Record<string, unknown>;
}

// Deduplication: track recently seen event IDs to prevent double-rendering
const _seenEvents = new Set<string>();
const _MAX_SEEN = 200;

function isDuplicate(msg: RawEvent): boolean {
  // Events that create new UI elements need dedup; status updates don't
  const kind = msg.kind;
  if (!msg.id) return false;
  // Only dedup events that push new cards
  if (kind === "status_update" || kind === "llm.content.chunk" || kind === "chat_update" ||
      kind === "shell.output" || kind === "llm.reasoning.chunk" || kind === "pong" || kind === "ping") {
    return false;
  }
  const key = `${msg.id}:${kind}`;
  if (_seenEvents.has(key)) return true;
  _seenEvents.add(key);
  // Prune old entries
  if (_seenEvents.size > _MAX_SEEN) {
    const iter = _seenEvents.values();
    for (let i = 0; i < 50; i++) iter.next();
    // Just clear and re-add won't work cleanly; use a simple approach
    _seenEvents.clear();
  }
  return false;
}

function ensureTurn(): string {
  const store = useWorkspaceStore.getState();
  if (store.activeTurnId) return store.activeTurnId;
  return store.startTurn();
}

function getStreamingTextEvent(turnId: string) {
  const store = useWorkspaceStore.getState();
  const turn = store.turns.find((t) => t.id === turnId);
  return turn?.events.filter((e) => e.type === "text" && (e as any).isStreaming).pop();
}

function pushThinking(turnId: string, label: string) {
  const store = useWorkspaceStore.getState();
  const turn = store.turns.find((t) => t.id === turnId);
  const existing = turn?.events.find((e) => e.type === "thinking" && (e as any).isActive);
  if (existing) return;
  store.pushEvent(turnId, {
    type: "thinking",
    id: nextEventId(),
    label,
    isActive: true,
    ts: Date.now(),
  });
}

function deactivateThinking(turnId: string) {
  const store = useWorkspaceStore.getState();
  const turn = store.turns.find((t) => t.id === turnId);
  if (!turn) return;
  const activeEvents = turn.events.filter((e) => e.type === "thinking" && (e as any).isActive);
  for (const evt of activeEvents) {
    store.updateEvent(turnId, evt.id, { isActive: false });
  }
}

function handleEvent(msg: RawEvent) {
  const store = useWorkspaceStore.getState();
  const ui = useUIStore.getState();

  // Skip duplicate events (from reconnections or double-delivery)
  if (isDuplicate(msg)) return;

  // ══════════════════════════════════════════════════════════════
  // LEGACY FORMAT (from existing desktop_server.py)
  // ══════════════════════════════════════════════════════════════

  switch (msg.kind) {

    // ── chat_update: streaming text ─────────────────────────────
    case "chat_update": {
      const type = msg.data?.type as string;
      const turnId = ensureTurn();

      if (type === "start") {
        ui.setAgentStatus("thinking");
        deactivateThinking(turnId);
        // Reset stream filtering state for new content
        _accum = "";
        _reasoningMode = false;
        const evtId = nextEventId();
        store.pushEvent(turnId, {
          type: "text",
          id: evtId,
          content: "",
          isStreaming: true,
        });
      } else if (type === "chunk") {
        const piece = String(msg.data?.piece ?? "");
        // Filter out raw tool-call XML/chunks
        if (isToolCallChunk(piece)) break;

        // Check if this turn already has tool calls — if so, suppress streaming text
        // (it's likely internal reasoning between tool calls, not user-facing content)
        const currentTurn = store.turns.find((t) => t.id === turnId);
        const hasTools = currentTurn?.events.some((e) => e.type === "tool_call" || e.type === "shell" || e.type === "diff");
        if (hasTools) {
          // Buffer it silently — will be cleaned and shown at "end" if it's real content
          const textEvt = getStreamingTextEvent(turnId);
          if (textEvt) {
            store.appendText(turnId, textEvt.id, piece);
          }
          break;
        }

        const textEvt = getStreamingTextEvent(turnId);
        if (textEvt) {
          store.appendText(turnId, textEvt.id, piece);
        } else {
          const evtId = nextEventId();
          store.pushEvent(turnId, {
            type: "text",
            id: evtId,
            content: piece,
            isStreaming: true,
          });
        }
      } else if (type === "end") {
        const textEvt = getStreamingTextEvent(turnId);
        if (textEvt) {
          // Clean any residual tool-call fragments from the final content
          const turn = store.turns.find((t) => t.id === turnId);
          const evt = turn?.events.find((e) => e.id === textEvt.id);
          if (evt && evt.type === "text") {
            const cleaned = stripToolCallXml((evt as any).content);
            if (cleaned !== (evt as any).content) {
              store.updateEvent(turnId, textEvt.id, { content: cleaned });
            }
          }
          store.endStreaming(turnId, textEvt.id);
        }
      }
      break;
    }

    // ── status_update ───────────────────────────────────────────
    case "status_update": {
      const status = msg.data?.status as string;
      const turnId = ensureTurn();

      if (status === "idle") {
        ui.setAgentStatus("idle");
        deactivateThinking(turnId);
      } else if (status === "thinking") {
        ui.setAgentStatus("thinking");
        pushThinking(turnId, "Thinking…");
      } else if (status === "running") {
        ui.setAgentStatus("executing");
        deactivateThinking(turnId);
      } else if (status === "planning") {
        ui.setAgentStatus("planning");
        deactivateThinking(turnId);
        pushThinking(turnId, "Planning…");
      }
      break;
    }

    // ── action_update: tool calls ───────────────────────────────
    case "action_update": {
      const turnId = ensureTurn();
      const toolId = String(msg.data?.tool_id ?? msg.id ?? nextEventId());
      const status = msg.data?.status as string;

      if (status === "running") {
        ui.setAgentStatus("executing");
        deactivateThinking(turnId);
        // Extract tool name: prefer 'name' field, fall back to label
        const toolName = String(msg.data?.name ?? "").replace(/[…\s].*/g, "").trim() || "tool";
        store.pushEvent(turnId, {
          type: "tool_call",
          id: toolId,
          tool: toolName,
          args: (msg.data?.args as Record<string, unknown>) ?? {},
          status: "running",
          ts: Date.now(),
        });
      } else if (status === "done") {
        store.completeToolCall(turnId, toolId, String(msg.data?.result ?? ""), msg.data?.runtime as number | undefined);
      } else if (status === "failed" || status === "denied") {
        store.failToolCall(turnId, toolId, String(msg.data?.error ?? "Failed"));
      }
      break;
    }

    // ── file_patch_ready: diff preview ──────────────────────────
    case "file_patch_ready": {
      const turnId = ensureTurn();
      ui.setAgentStatus("editing");
      store.pushEvent(turnId, {
        type: "diff",
        id: msg.id ?? nextEventId(),
        path: String(msg.data?.path ?? ""),
        additions: (msg.data?.additions as number) ?? 0,
        deletions: (msg.data?.deletions as number) ?? 0,
        hunks: (msg.data?.hunks as any[]) ?? [],
        status: "pending",
      });
      break;
    }

    // ── approval_request ────────────────────────────────────────
    case "approval_request": {
      const turnId = ensureTurn();
      ui.setAgentStatus("waiting_approval");
      store.pushEvent(turnId, {
        type: "approval_required",
        id: msg.id ?? nextEventId(),
        message: String(msg.data?.message ?? "Action requires approval"),
        tool: msg.data?.tool as string,
        risk: (msg.data?.risk as "low" | "medium" | "high") ?? "medium",
        detail: msg.data?.detail as string,
      });
      break;
    }

    // ── session_event: turn lifecycle ───────────────────────────
    case "session_event": {
      const event = msg.data?.event as string;
      if (event === "turn_end" || event === "interrupted") {
        const turnId = store.activeTurnId;
        if (turnId) {
          deactivateThinking(turnId);
          ui.setAgentStatus("done");
          ui.setBrowserActive(false);
          store.endTurn(turnId, event === "interrupted" ? "error" : "done");
        }
      }
      break;
    }

    // ══════════════════════════════════════════════════════════════
    // NEW FORMAT (future backend events)
    // ══════════════════════════════════════════════════════════════

    // ── LLM streaming ─────────────────────────────────────────
    case "llm.content.start": {
      const turnId = ensureTurn();
      ui.setAgentStatus("thinking");
      deactivateThinking(turnId);
      // Reset stream filtering state
      _accum = "";
      _reasoningMode = false;
      const evtId = nextEventId();
      store.pushEvent(turnId, {
        type: "text",
        id: evtId,
        content: "",
        isStreaming: true,
      });
      break;
    }

    case "llm.content.chunk": {
      const turnId = store.activeTurnId;
      if (!turnId) break;
      const piece = (msg.data?.piece as string) ?? "";
      if (isToolCallChunk(piece)) break;
      // Suppress streaming text if tools have run (it's likely reasoning between tools)
      const turnForChunk = store.turns.find((t) => t.id === turnId);
      const turnHasTools = turnForChunk?.events.some((e) => e.type === "tool_call" || e.type === "shell" || e.type === "diff");
      const textEvt = getStreamingTextEvent(turnId);
      if (textEvt) {
        if (turnHasTools) {
          // Buffer silently, will be cleaned at end
          store.appendText(turnId, textEvt.id, piece);
        } else {
          store.appendText(turnId, textEvt.id, piece);
        }
      }
      break;
    }

    case "llm.content.end": {
      const turnId = store.activeTurnId;
      if (!turnId) break;
      const textEvt = getStreamingTextEvent(turnId);
      if (textEvt) {
        // Clean any residual tool-call fragments
        const turn = store.turns.find((t) => t.id === turnId);
        const evt = turn?.events.find((e) => e.id === textEvt.id);
        if (evt && evt.type === "text") {
          const cleaned = stripToolCallXml((evt as any).content);
          if (cleaned !== (evt as any).content) {
            store.updateEvent(turnId, textEvt.id, { content: cleaned });
          }
        }
        store.endStreaming(turnId, textEvt.id);
      }
      break;
    }

    // ── Planning ──────────────────────────────────────────────
    case "plan.created": {
      const turnId = ensureTurn();
      ui.setAgentStatus("planning");
      const steps = ((msg.data?.steps as Array<{ id?: string; label?: string }>) ?? []).map((s, i) => ({
        id: s.id ?? `step-${i}`,
        label: s.label ?? `Step ${i + 1}`,
        status: "pending" as const,
      }));
      store.pushEvent(turnId, {
        type: "plan",
        id: msg.id ?? nextEventId(),
        steps,
        collapsed: false,
      });
      break;
    }

    case "plan.step.update": {
      const turnId = store.activeTurnId;
      if (!turnId) break;
      const planId = msg.data?.plan_id as string;
      const stepId = msg.data?.step_id as string;
      const status = msg.data?.status as "pending" | "active" | "done" | "failed";
      if (planId && stepId && status) {
        store.updatePlanStep(turnId, planId, stepId, status);
      }
      break;
    }

    // ── Tool calls ────────────────────────────────────────────
    case "tool.start": {
      const turnId = ensureTurn();
      ui.setAgentStatus("executing");
      deactivateThinking(turnId);
      store.pushEvent(turnId, {
        type: "tool_call",
        id: msg.id ?? nextEventId(),
        tool: (msg.data?.name as string) ?? "unknown",
        args: (msg.data?.args as Record<string, unknown>) ?? {},
        status: "running",
        ts: Date.now(),
      });
      break;
    }

    case "tool.result": {
      const turnId = store.activeTurnId;
      if (!turnId) break;
      const toolId = msg.data?.call_id as string ?? msg.id;
      if (toolId) {
        store.completeToolCall(turnId, toolId, (msg.data?.result as string) ?? "", msg.data?.runtime as number | undefined);
      }
      break;
    }

    case "tool.error": {
      const turnId = store.activeTurnId;
      if (!turnId) break;
      const toolId = msg.data?.call_id as string ?? msg.id;
      if (toolId) {
        store.failToolCall(turnId, toolId, (msg.data?.error as string) ?? "Failed");
      }
      break;
    }

    // ── Agents ────────────────────────────────────────────────
    case "agent.spawned": {
      const turnId = ensureTurn();
      store.pushEvent(turnId, {
        type: "agent_update",
        id: msg.id ?? nextEventId(),
        name: (msg.data?.name as string) ?? "Agent",
        role: (msg.data?.role as string) ?? "",
        task: (msg.data?.task as string) ?? "",
        status: "running",
        ts: Date.now(),
      });
      break;
    }

    case "agent.done": {
      const turnId = store.activeTurnId;
      if (!turnId) break;
      const agentId = msg.data?.agent_id as string ?? msg.id;
      if (agentId) {
        store.updateEvent(turnId, agentId, { status: "success" });
      }
      break;
    }

    // ── Diffs (new format) ────────────────────────────────────
    case "write.preview": {
      const turnId = ensureTurn();
      ui.setAgentStatus("editing");
      store.pushEvent(turnId, {
        type: "diff",
        id: msg.id ?? nextEventId(),
        path: (msg.data?.path as string) ?? "",
        additions: (msg.data?.additions as number) ?? 0,
        deletions: (msg.data?.deletions as number) ?? 0,
        hunks: (msg.data?.hunks as any[]) ?? [],
        status: "pending",
      });
      break;
    }

    // ── Approvals (new format) ────────────────────────────────
    case "approval.request": {
      const turnId = ensureTurn();
      ui.setAgentStatus("waiting_approval");
      store.pushEvent(turnId, {
        type: "approval_required",
        id: msg.id ?? nextEventId(),
        message: (msg.data?.message as string) ?? "Action requires approval",
        tool: msg.data?.tool as string,
        risk: (msg.data?.risk as "low" | "medium" | "high") ?? "medium",
        detail: msg.data?.detail as string,
      });
      break;
    }

    // ── Run lifecycle ─────────────────────────────────────────
    case "run.summary": {
      const turnId = store.activeTurnId;
      if (!turnId) break;
      deactivateThinking(turnId);
      ui.setAgentStatus("done");
      store.pushEvent(turnId, {
        type: "final_response",
        id: nextEventId(),
        summary: (msg.data?.summary as string) ?? "Task complete.",
        filesModified: (msg.data?.files_modified as string[]) ?? [],
        nextSteps: (msg.data?.next_steps as string[]) ?? [],
        commands: msg.data?.commands as string[],
      });
      store.endTurn(turnId, "done");
      break;
    }

    case "run.error": {
      const turnId = store.activeTurnId;
      if (!turnId) break;
      deactivateThinking(turnId);
      ui.setAgentStatus("error");
      store.endTurn(turnId, "error");
      break;
    }

    // ── Shell streaming ──────────────────────────────────────
    case "shell.run":
    case "SHELL_RUN": {
      const turnId = ensureTurn();
      ui.setAgentStatus("executing");
      deactivateThinking(turnId);
      const shellId = msg.id ?? nextEventId();
      const command = (msg.data?.command as string) ?? "shell";
      const cwd = msg.data?.cwd as string | undefined;
      store.pushEvent(turnId, {
        type: "shell",
        id: shellId,
        command,
        cwd,
        output: "",
        isRunning: true,
      });
      break;
    }

    case "shell.output": {
      const turnId = store.activeTurnId;
      if (!turnId) break;
      const shellId = msg.id;
      if (!shellId) break;
      const piece = (msg.data?.piece as string) ?? "";
      const stream = (msg.data?.stream as string) ?? "stdout";
      // Mark stderr lines with a control character prefix for rendering
      const chunk = stream === "stderr" ? `\x02stderr:${piece}` : piece;
      store.appendShellOutput(turnId, shellId, chunk);
      break;
    }

    case "shell.end":
    case "SHELL_END": {
      const turnId = store.activeTurnId;
      if (!turnId) break;
      // Find the running shell event (IDs differ between shell.run and shell.end)
      const turn = store.turns.find((t) => t.id === turnId);
      const runningShell = turn?.events.filter((e) => e.type === "shell" && (e as any).isRunning).pop();
      const shellId = runningShell?.id ?? msg.id;
      if (!shellId) break;
      const exitCode = (msg.data?.exit_code as number) ?? 0;
      const runtime = msg.data?.runtime as number | undefined;
      const output = (msg.data?.output as string) ?? "";
      if (output) {
        store.appendShellOutput(turnId, shellId, output);
      }
      store.endShell(turnId, shellId, exitCode, runtime);
      break;
    }

    // ── Reasoning/Thinking (chain-of-thought) ────────────────
    case "llm.reasoning.start": {
      const turnId = ensureTurn();
      deactivateThinking(turnId);
      const evtId = msg.id ?? nextEventId();
      store.pushEvent(turnId, {
        type: "reasoning",
        id: evtId,
        content: "",
        isStreaming: true,
        collapsed: false,
      });
      break;
    }

    case "llm.reasoning.chunk":
    case "LLM_REASONING_CHUNK": {
      const turnId = store.activeTurnId;
      if (!turnId) break;
      const piece = (msg.data?.piece as string) ?? "";
      if (!piece) break;
      // Find the active reasoning event
      const turn = store.turns.find((t) => t.id === turnId);
      const reasoningEvt = turn?.events
        .filter((e) => e.type === "reasoning" && (e as any).isStreaming)
        .pop();
      if (reasoningEvt) {
        store.appendReasoning(turnId, reasoningEvt.id, piece);
      } else {
        // No active reasoning event — create one (legacy format without start signal)
        const evtId = msg.id ?? nextEventId();
        store.pushEvent(turnId, {
          type: "reasoning",
          id: evtId,
          content: piece,
          isStreaming: true,
          collapsed: false,
        });
      }
      break;
    }

    case "llm.reasoning.end": {
      const turnId = store.activeTurnId;
      if (!turnId) break;
      const turn2 = store.turns.find((t) => t.id === turnId);
      const activeReasoning = turn2?.events
        .filter((e) => e.type === "reasoning" && (e as any).isStreaming)
        .pop();
      if (activeReasoning) {
        store.endReasoning(turnId, activeReasoning.id);
      }
      break;
    }

    // ── Token usage ──────────────────────────────────────────
    case "llm.usage": {
      const turnId = store.activeTurnId;
      if (!turnId) break;
      // Backend sends: turn_in, turn_out, session_in, session_out
      const prompt = (msg.data?.turn_in as number) ?? (msg.data?.prompt_tokens as number) ?? 0;
      const completion = (msg.data?.turn_out as number) ?? (msg.data?.completion_tokens as number) ?? 0;
      const total = (msg.data?.total_tokens as number) ?? (prompt + completion);
      const usage = { prompt, completion, total };
      store.updateTurnTokens(turnId, usage);
      ui.addSessionTokens(usage);
      break;
    }

    case "token.budget": {
      const used = (msg.data?.used as number) ?? 0;
      const limit = (msg.data?.limit as number) ?? 0;
      const remaining = (msg.data?.remaining as number) ?? 0;
      ui.setTokenBudget({ used, limit, remaining });
      break;
    }

    // ── Mission / multi-agent orchestration ─────────────────────
    case "mission.start": {
      const turnId = ensureTurn();
      const agents = ((msg.data?.agents as Array<{ id?: string; name?: string; task?: string }>) ?? []).map((a, i) => ({
        id: a.id ?? `agent-${i}`,
        name: a.name ?? `Agent ${i + 1}`,
        task: a.task ?? "",
        status: "queued" as const,
      }));
      store.pushEvent(turnId, {
        type: "mission",
        id: msg.id ?? nextEventId(),
        title: (msg.data?.title as string) ?? "Mission",
        agents,
        progress: 0,
        status: "running",
        collapsed: false,
      });
      break;
    }

    case "mission.progress": {
      const turnId = store.activeTurnId;
      if (!turnId) break;
      const missionId = msg.id;
      if (!missionId) break;
      const agentId = msg.data?.agent_id as string;
      const status = (msg.data?.status as "queued" | "running" | "done" | "failed") ?? "running";
      const progress = (msg.data?.progress as number) ?? 0;
      if (agentId) {
        store.updateMissionProgress(turnId, missionId, agentId, status, progress);
      }
      break;
    }

    case "mission.complete": {
      const turnId = store.activeTurnId;
      if (!turnId) break;
      const missionId = msg.id;
      if (!missionId) break;
      const success = (msg.data?.success as boolean) ?? true;
      const summary = (msg.data?.summary as string) ?? "";
      const duration = msg.data?.duration_s as number | undefined;
      store.completeMission(turnId, missionId, success, summary, duration);
      break;
    }

    case "agent.task.start": {
      const turnId = store.activeTurnId;
      if (!turnId) break;
      const missionId = msg.id;
      if (!missionId) break;
      const agentId = msg.data?.agent_id as string;
      if (agentId) {
        store.updateMissionProgress(turnId, missionId, agentId, "running", 0);
      }
      break;
    }

    // ── Reflection ──────────────────────────────────────────────
    case "reflection": {
      const turnId = ensureTurn();
      const category = (msg.data?.category as "failure_analysis" | "success_pattern" | "improvement") ?? "improvement";
      const insight = (msg.data?.insight as string) ?? "";
      if (insight) {
        store.pushEvent(turnId, {
          type: "reflection",
          id: msg.id ?? nextEventId(),
          insight,
          category,
        });
      }
      break;
    }

    // ── Pipeline stage progress ──────────────────────────────────────
    case "stage.progress": {
      const idx = (msg.data?.index as number) ?? 0;
      const name = (msg.data?.name as string) ?? "";
      const status = (msg.data?.status as string) ?? "";
      const detail = (msg.data?.detail as string) ?? "";
      const mappedStatus = status === "running" ? "active" : status === "done" ? "done" : "pending";
      if (mappedStatus === "active") {
        ui.setAgentStatus("executing");
      }
      store.updatePipelineStage(idx, name, mappedStatus as any, detail || undefined);
      if (mappedStatus === "done") {
        if (store.pipelineStages.every((s) => s.status === "done" || s.status === "failed")) {
          store.resetPipeline();
        }
      }
      break;
    }

    // ── Browser active — switch layout to show browser status panel ────
    case "browser.active": {
      ui.setBrowserActive(true);
      break;
    }

    // ── Browser navigate — update panel status ────
    case "browser.navigate": {
      break;
    }
  }
}

// ── Content filtering ───────────────────────────────────────────────────────
// The LLM streams raw tool-call XML, internal reasoning/chain-of-thought,
// and </think> blocks as visible content. Filter aggressively.

const TOOL_CALL_PATTERNS = [
  /<tool_call>/i, /<\/tool_call>/i,
  /<function=/i, /<parameter=/i,
  /<\/function>/i, /<\/parameter>/i,
  /^<\/?tool_c/, /^<\/?function/, /^<\/?parameter/,
  /rameter=/i,  // partial <parameter= fragment
  /unction=/i,  // partial <function= fragment
  /parameter>/i, // closing fragment
];

const PLAIN_TOOL_CALL_RE =
  /(?:write_file|read_file|run_command|create_file|delete_file|list_files|search_code|grep_search|web_search|todo_write|git_commit|git_diff|subagent)(?:\s*\(|\s+)(?:path|command|content|query|pattern|url|items)\s*=/i;

// Also catch fragments like "rameter=path> value" or "ameter=content>"
const PARTIAL_XML_TOOL_RE = /(?:^|[a-z])meter=(?:path|content|command|query)|_>$/i;

const THINK_TAG_RE = /<\/?think(?:ing)?>/i;

const REASONING_STARTERS_RE = /^(?:Maybe|Perhaps|Let me|I think|Given the|But (?:the|I|that|again|also|wait|which)|Another|Could be|That (?:seems|would|could|is|doesn't)|Unless|Alternatively|However|Thus|Therefore|I'll|I could|I need to|I should|I don't|I can|The user|The instruction|Maybe the user|Looking at|Also,|I might|I'll proceed|So I|But also|But wait)/m;

// Stream-level accumulator to detect reasoning across small chunks
let _accum = "";
let _reasoningMode = false;

function isToolCallChunk(piece: string): boolean {
  const trimmed = piece.trim();
  if (!trimmed) return false;

  // XML-style tool calls and fragments
  if (TOOL_CALL_PATTERNS.some((p) => p.test(trimmed))) return true;

  // Partial XML tool fragments (e.g. "rameter=path> C:\Users\...")
  if (PARTIAL_XML_TOOL_RE.test(trimmed)) return true;

  // </think> tags — suppress
  if (THINK_TAG_RE.test(trimmed)) {
    _reasoningMode = false;
    return true;
  }

  // Plain-text tool calls
  if (PLAIN_TOOL_CALL_RE.test(trimmed)) return true;

  // Accumulate text to detect reasoning mode
  _accum += piece;

  // If already in reasoning mode, keep suppressing
  if (_reasoningMode) return true;

  // Catch partial tool-call fragments like "rameter=path>" or "ontent="
  if (/(?:ameter|ontent|ommand|unction)\s*=/.test(trimmed)) return true;
  if (/^[a-z_]+=/.test(trimmed) && trimmed.length < 80) return true;

  // Catch reasoning that starts mid-sentence (continuation from filtered chunk)
  // e.g. "he user might want to..." (was "The user..." but "T" was in prev chunk)
  if (/\b(?:user might|I'll run|Let's do|I need to|I can use|So:|file path contains)\b/i.test(trimmed)) {
    _reasoningMode = true;
    return true;
  }

  // Check accumulated buffer for reasoning pattern density
  if (_accum.length > 80) {
    const matches = _accum.match(new RegExp(REASONING_STARTERS_RE.source, "gm"));
    if (matches && matches.length >= 2) {
      _reasoningMode = true;
      return true;
    }
  }

  return false;
}

function stripToolCallXml(content: string): string {
  // Reset stream state
  _accum = "";
  _reasoningMode = false;

  let cleaned = content;

  // Remove everything before the LAST </think> tag (it's all reasoning)
  const thinkMatch = cleaned.match(/[\s\S]*<\/think(?:ing)?>\s*/i);
  if (thinkMatch) {
    cleaned = cleaned.slice(thinkMatch[0].length);
  }

  // Remove inline <think>...</think> blocks
  cleaned = cleaned.replace(/<think(?:ing)?>[\s\S]*?<\/think(?:ing)?>/gi, "");
  cleaned = cleaned.replace(/<\/?think(?:ing)?>/gi, "");

  // Remove tool_call XML
  cleaned = cleaned.replace(/<tool_call>[\s\S]*?<\/tool_call>/gi, "");
  cleaned = cleaned.replace(/<\/?tool_call>/gi, "");
  cleaned = cleaned.replace(/<function=[^>]*>[\s\S]*?<\/function>/gi, "");
  cleaned = cleaned.replace(/<parameter=[^>]*>[\s\S]*?<\/parameter>/gi, "");
  cleaned = cleaned.replace(/<\/?function[^>]*>/gi, "");
  cleaned = cleaned.replace(/<\/?parameter[^>]*>/gi, "");

  // Remove plain-text tool call lines
  cleaned = cleaned.replace(
    /(?:write_file|read_file|run_command|create_file|delete_file|list_files|search_code|grep_search|todo_write)(?:\s*\(|\s+)(?:path|command|content|query|pattern|url|items)\s*=[^\n]*/gi,
    ""
  );
  // Remove partial XML tool fragments (rameter=path> ..., etc.)
  cleaned = cleaned.replace(/[a-z]*meter=(?:path|content|command|query)[^}\n]*/gi, "");

  // Remove paragraphs that are internal reasoning
  const paragraphs = cleaned.split(/\n\n+/);
  const kept = paragraphs.filter((para) => {
    const t = para.trim();
    if (!t || t.length < 5) return false;
    if (/\bThe user (?:wants|expects|might|is|said)\b/i.test(t)) return false;
    if (/\b(?:PARALLEL RULE|SAME response|function_calls|tool calls together)\b/i.test(t)) return false;
    if (/\bI (?:need to|should|could|must|might|can't|don't)\b.*\b(?:call|emit|write_file|run_command|read_file|todo_write)\b/i.test(t)) return false;
    const lines = t.split("\n").filter((l) => l.trim());
    if (lines.length >= 2) {
      const reasoningCount = lines.filter((l) => REASONING_STARTERS_RE.test(l.trim())).length;
      if (reasoningCount / lines.length > 0.5) return false;
    }
    return true;
  });
  cleaned = kept.join("\n\n");

  // De-interleave duplicated content (degenerate model output)
  cleaned = deinterleave(cleaned);

  // Remove duplicated sentences
  cleaned = removeDuplicates(cleaned);

  cleaned = cleaned.replace(/\n{3,}/g, "\n\n").trim();

  // If after all cleanup the content is empty or just whitespace/tags, return empty
  if (!cleaned.replace(/[<>\s]/g, "")) return "";

  return cleaned;
}

function removeDuplicates(text: string): string {
  if (text.length < 80) return text;
  // Split into sentences and deduplicate
  const parts = text.split(/(?<=[.!?])\s+/);
  if (parts.length < 3) return text;
  const seen = new Set<string>();
  const result: string[] = [];
  for (const part of parts) {
    const key = part.trim().toLowerCase().replace(/\s+/g, " ");
    if (key.length > 15 && seen.has(key)) continue;
    seen.add(key);
    result.push(part);
  }
  return result.join(" ");
}

/**
 * Detect and fix interleaved duplicate text.
 * When the model outputs "abcabc" where "abc" is duplicated character-by-character,
 * the result looks like "aabbcc". This deinterleaves it back to "abc".
 */
function deinterleave(text: string): string {
  if (text.length < 20) return text;

  // Process line by line — interleaving typically happens within lines
  const lines = text.split("\n");
  const fixed = lines.map((line) => {
    if (line.length < 10) return line;

    // Check if the line is a character-by-character interleave of two copies
    // Test: take every other character and see if both halves are the same
    if (line.length % 2 === 0) {
      let half1 = "";
      let half2 = "";
      for (let i = 0; i < line.length; i++) {
        if (i % 2 === 0) half1 += line[i];
        else half2 += line[i];
      }
      if (half1 === half2 && half1.length > 5) {
        return half1;
      }
    }

    // Check for word-level duplication: "word1 word1 word2 word2" → "word1 word2"
    const words = line.split(/\s+/);
    if (words.length >= 4) {
      const half = Math.floor(words.length / 2);
      const firstHalf = words.slice(0, half).join(" ");
      const secondHalf = words.slice(half, half * 2).join(" ");
      if (firstHalf === secondHalf && firstHalf.length > 10) {
        // The line is the first half repeated — keep just the first half + any remainder
        const remainder = words.slice(half * 2).join(" ");
        return remainder ? `${firstHalf} ${remainder}` : firstHalf;
      }
    }

    // Check for substring duplication: "abcdefabcdef" where first half === second half
    if (line.length >= 20) {
      const halfLen = Math.floor(line.length / 2);
      const first = line.slice(0, halfLen);
      const second = line.slice(halfLen, halfLen * 2);
      if (first === second) {
        const tail = line.slice(halfLen * 2);
        return first + tail;
      }
    }

    return line;
  });

  return fixed.join("\n");
}
