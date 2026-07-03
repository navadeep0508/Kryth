import React, { memo, useRef, useEffect, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  useWorkspaceStore,
  type Turn, type WorkspaceEvent, type ToolCallEvent,
  type ShellEvent, type DiffEvent, type PlanEvent, type PlanStep,
  type ReasoningEvent, type AgentEvent, type ReflectionEvent,
  type TextEvent, type ThinkingEvent, type MissionEvent,
} from "@/store/workspaceStore";
import { ApprovalCard } from "./cards/ApprovalCard";
import { useEditorStore } from "@/store/editorStore";
import { cn } from "@/lib/utils";
import { useUIStore } from "@/store/uiStore";
import {
  Loader2, Check, X, FileCode, Terminal, Search, Globe, Play,
  ChevronDown, ChevronRight, Sparkles, Brain, Bot, User,
  Lightbulb, Rocket, CheckCircle2, FileText, ArrowRight,
  AlertTriangle, RotateCcw,
} from "lucide-react";

export default memo(function Workspace() {
  const turns = useWorkspaceStore((s) => s.turns);
  const scrollRef = useRef<HTMLDivElement>(null);
  const autoScroll = useRef(true);

  useEffect(() => {
    if (autoScroll.current && scrollRef.current) {
      scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
    }
  }, [turns]);

  const handleScroll = useCallback(() => {
    if (!scrollRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
    autoScroll.current = scrollHeight - scrollTop - clientHeight < 80;
  }, []);

  if (turns.length === 0) return <EmptyState />;

  return (
    <div ref={scrollRef} onScroll={handleScroll} className="h-full overflow-y-auto">
      <div className="py-3 px-4 space-y-2">
        {turns.map((turn) => <TurnBlock key={turn.id} turn={turn} />)}
        <div className="h-8" />
      </div>
    </div>
  );
});

const TurnBlock = memo(function TurnBlock({ turn }: { turn: Turn }) {
  const retryLast = useWorkspaceStore((s) => s.retryLast);
  const hasFinal = turn.events.some((e) => e.type === "final_response" || (e.type === "text" && !(e as TextEvent).isStreaming && (e as TextEvent).content.length > 0));
  const showError = turn.status === "error" && !hasFinal;

  const diffPaths = new Set(turn.events.filter((e) => e.type === "diff").map((e) => (e as DiffEvent).path));
  const shellCommands = new Set(turn.events.filter((e) => e.type === "shell").map((e) => (e as ShellEvent).command));
  const hasShell = shellCommands.size > 0;

  const seenKeys = new Set<string>();
  const filteredEvents = turn.events.filter((evt) => {
    if (evt.type === "tool_call") {
      const tc = evt as ToolCallEvent;
      if (FILE_WRITE_TOOLS.has(tc.tool)) {
        const path = String(tc.args.path ?? tc.args.file_path ?? "");
        if (path && diffPaths.has(path)) return false;
      }
      if (CMD_TOOLS.has(tc.tool) && hasShell) return false;
      const key = `${tc.tool}:${String(tc.args.path ?? tc.args.command ?? tc.id)}`;
      if (seenKeys.has(key)) return false;
      seenKeys.add(key);
    }
    if (evt.type === "shell") { const se = evt as ShellEvent; const key = `shell:${se.command}`; if (seenKeys.has(key)) return false; seenKeys.add(key); }
    if (evt.type === "diff") { const de = evt as DiffEvent; const key = `diff:${de.path}`; if (seenKeys.has(key)) return false; seenKeys.add(key); }
    return true;
  });

  const seenTextContent = new Set<string>();
  const displayEvents = filteredEvents.filter((evt: WorkspaceEvent) => {
    if (evt.type === "text") {
      const te = evt as TextEvent;
      if (!te.content?.trim() && !te.isStreaming) return false;
      const turnHasTools = filteredEvents.some((e) => e.type === "tool_call" || e.type === "shell" || e.type === "diff");
      if (turnHasTools) return false;
      const content = te.content?.trim();
      if (content && content.length > 10) {
        const key = content.slice(0, 80).toLowerCase();
        if (seenTextContent.has(key)) return false;
        seenTextContent.add(key);
      }
    }
    if (evt.type === "thinking") {
      const hasCompletedTool = filteredEvents.some((e) => (e.type === "tool_call" && (e as ToolCallEvent).status === "success") || e.type === "diff");
      if (hasCompletedTool) return false;
    }
    return true;
  });

  return (
    <div className="space-y-1">
      {displayEvents.map((evt) => <EventCard key={evt.id} event={evt} turnId={turn.id} />)}
      {turn.status === "active" && <LiveActivity turn={turn} />}
      {showError && <ErrorBlock onRetry={retryLast} />}
    </div>
  );
});

function EventCard({ event, turnId }: { event: WorkspaceEvent; turnId: string }) {
  switch (event.type) {
    case "user_prompt": return <UserBlock content={(event as any).content} />;
    case "thinking": return (event as ThinkingEvent).isActive ? <ThinkingLine /> : null;
    case "reasoning": return <ReasoningBlock event={event as ReasoningEvent} turnId={turnId} />;
    case "plan": return null;
    case "tool_call": return <ToolBlock event={event as ToolCallEvent} />;
    case "shell": return <ShellBlock event={event as ShellEvent} />;
    case "diff": return <DiffBlock event={event as DiffEvent} turnId={turnId} />;
    case "approval_required": return (event as any).resolved ? null : <ApprovalCard event={event as any} turnId={turnId} />;
    case "text": return <TextBlock event={event as TextEvent} />;
    case "final_response": return <FinalBlock event={event as any} />;
    case "agent_update": return <AgentBlock event={event as AgentEvent} />;
    case "mission": return <MissionBlock event={event as MissionEvent} turnId={turnId} />;
    case "reflection": return <ReflectionBlock event={event as ReflectionEvent} />;
    default: return null;
  }
}

/* ── User prompt ───────────────────────────────────────────── */
function UserBlock({ content }: { content: string }) {
  return (
    <div className="flex items-start gap-2 px-3 py-1.5 border-l-2 border-accent/30 bg-accent/[0.02]">
      <span className="text-[10px] font-mono text-accent shrink-0 mt-0.5">❯</span>
      <p className="text-[12.5px] text-text font-mono leading-relaxed whitespace-pre-wrap break-words">{content}</p>
    </div>
  );
}

/* ── Thinking indicator ────────────────────────────────────── */
function ThinkingLine() {
  return (
    <div className="flex items-center gap-1.5 px-3 py-1">
      <span className="flex gap-0.5">
        <span className="w-1 h-1 bg-dim rounded-full animate-pulse-dot" />
        <span className="w-1 h-1 bg-dim rounded-full animate-pulse-dot" style={{ animationDelay: "0.2s" }} />
        <span className="w-1 h-1 bg-dim rounded-full animate-pulse-dot" style={{ animationDelay: "0.4s" }} />
      </span>
      <span className="text-[10px] text-dim">Thinking</span>
    </div>
  );
}

/* ── Live activity ─────────────────────────────────────────── */
function LiveActivity({ turn }: { turn: Turn }) {
  const [elapsed, setElapsed] = React.useState(0);
  const startRef = useRef(Date.now());
  const agentStatus = useUIStore((s) => s.agentStatus);
  const currentActivity = getActivity(turn);
  const activityKey = currentActivity.label;

  useEffect(() => {
    startRef.current = Date.now();
    setElapsed(0);
    if (!currentActivity.active) return;
    const interval = setInterval(() => setElapsed(Math.floor((Date.now() - startRef.current) / 1000)), 1000);
    return () => clearInterval(interval);
  }, [activityKey, currentActivity.active]);

  if (agentStatus === "idle" || agentStatus === "done") return null;
  if (!currentActivity.active) return null;

  return (
    <div className="flex items-center gap-1.5 px-3 py-1 bg-accent/[0.03] border-l-2 border-accent/30">
      <Loader2 size={9} className="animate-spin text-accent shrink-0" />
      <currentActivity.icon size={9} className="text-accent shrink-0" />
      <span className="text-[10px] text-muted flex-1">{currentActivity.label}</span>
      <span className="text-[9px] text-faint font-mono tabular-nums">{elapsed}s</span>
    </div>
  );
}

interface ActivityInfo { active: boolean; label: string; icon: React.ElementType }
function getActivity(turn: Turn): ActivityInfo {
  const events = turn.events;
  for (let i = events.length - 1; i >= 0; i--) {
    const evt = events[i];
    if (evt.type === "tool_call") {
      const tc = evt as ToolCallEvent;
      if (tc.status !== "running") continue;
      const path = String(tc.args.path ?? tc.args.file_path ?? "").replace(/\\/g, "/").split("/").pop() ?? "";
      const cmd = String(tc.args.command ?? tc.args.cmd ?? "").split(/[;&|]/)[0]?.trim() ?? "";
      if (["write_file","create_file"].includes(tc.tool)) return { active: true, label: `Writing ${path}…`, icon: FileCode };
      if (["edit_file","multi_edit"].includes(tc.tool)) return { active: true, label: `Editing ${path}…`, icon: FileCode };
      if (tc.tool === "read_file") return { active: true, label: `Reading ${path}…`, icon: FileCode };
      if (["run_command","execute"].includes(tc.tool)) return { active: true, label: `Running ${cmd}…`, icon: Terminal };
      if (["search_code","grep_search"].includes(tc.tool)) return { active: true, label: "Searching…", icon: Search };
      if (tc.tool === "web_search") return { active: true, label: "Searching web…", icon: Globe };
      return { active: true, label: `${tc.tool.replace(/_/g, " ")}…`, icon: Play };
    }
    if (evt.type === "shell") {
      const se = evt as ShellEvent;
      if (se.isRunning) return { active: true, label: `Running ${se.command.split(/[;&|]/)[0]?.trim() || "command"}…`, icon: Terminal };
    }
    if (evt.type === "thinking" && (evt as ThinkingEvent).isActive) return { active: true, label: "Thinking…", icon: Brain };
  }
  return { active: true, label: "Waiting…", icon: Brain };
}

/* ── Reasoning ─────────────────────────────────────────────── */
function ReasoningBlock({ event, turnId }: { event: ReasoningEvent; turnId: string }) {
  const toggle = useWorkspaceStore((s) => s.toggleReasoningCollapse);
  const words = event.content.split(/\s+/).filter(Boolean).length;
  return (
    <div>
      <button onClick={() => toggle(turnId, event.id)} className="flex items-center gap-1.5 px-3 py-0.5 text-[10px] text-dim hover:text-muted w-full text-left">
        {event.collapsed ? <ChevronRight size={9} /> : <ChevronDown size={9} />}
        <Brain size={9} className="text-dim" />
        <span>{event.collapsed ? `Thought ${words} words` : "Reasoning"}</span>
        {event.isStreaming && <Loader2 size={8} className="animate-spin text-dim" />}
      </button>
      {!event.collapsed && (
        <pre className="text-[10px] text-dim/60 font-mono leading-relaxed whitespace-pre-wrap max-h-[160px] overflow-y-auto px-6 pr-2">
          {event.content}{event.isStreaming && <span className="inline-block w-[1.5px] h-2.5 bg-accent/50 ml-0.5 animate-cursor-blink" />}
        </pre>
      )}
    </div>
  );
}

/* ── Tool dispatch ─────────────────────────────────────────── */
const FILE_WRITE_TOOLS = new Set(["write_file", "create_file", "edit_file"]);
const CMD_TOOLS = new Set(["run_command", "execute", "shell"]);
const HIDDEN_TOOLS = new Set(["exit_plan_mode", "todo_write", "task_output"]);

function ToolBlock({ event }: { event: ToolCallEvent }) {
  if (HIDDEN_TOOLS.has(event.tool)) return null;
  if (FILE_WRITE_TOOLS.has(event.tool)) return <FileWriteBlock event={event} />;
  if (CMD_TOOLS.has(event.tool)) return <CommandBlock event={event} />;
  return <CompactToolLine event={event} />;
}

/* ── File write block ──────────────────────────────────────── */
function FileWriteBlock({ event }: { event: ToolCallEvent }) {
  const [collapsed, setCollapsed] = React.useState(true);
  const filePath = String(event.args.path ?? event.args.file_path ?? "");
  const content = String(event.args.content ?? event.result ?? "");
  const filename = filePath.replace(/\\/g, "/").split("/").pop() ?? filePath;
  const lines = content ? content.split("\n") : [];

  return (
    <div className="border border-border bg-panel">
      <button onClick={() => setCollapsed(!collapsed)} className="w-full flex items-center gap-1.5 px-2 py-1 hover:bg-panel-hover transition-colors text-left">
        <StatusIcon status={event.status} />
        <FileCode size={9} className="text-dim" />
        <span className="text-[11px] font-mono text-text truncate">{filename}</span>
        {lines.length > 0 && <span className="text-[9px] text-success font-mono ml-auto">+{lines.length}</span>}
        {event.runtime != null && <span className="text-[9px] text-faint font-mono">{event.runtime.toFixed(1)}s</span>}
        {collapsed ? <ChevronRight size={9} className="text-dim" /> : <ChevronDown size={9} className="text-dim" />}
      </button>
      {!collapsed && lines.length > 0 && (
        <div className="max-h-[240px] overflow-y-auto font-mono text-[10px] leading-relaxed">
          {lines.map((line, i) => (
            <div key={i} className="flex bg-success/[0.04] border-l-2 border-success">
              <span className="w-8 shrink-0 text-right pr-1 text-faint select-none text-[9px]">{i + 1}</span>
              <span className="text-success/80 px-1">+</span>
              <span className="text-text/80">{line}</span>
            </div>
          ))}
        </div>
      )}
      {event.status === "running" && lines.length === 0 && (
        <div className="px-2 py-1 flex items-center gap-1.5"><Loader2 size={9} className="animate-spin text-accent" /><span className="text-[10px] text-dim">Writing…</span></div>
      )}
    </div>
  );
}

/* ── Command block ─────────────────────────────────────────── */
function CommandBlock({ event }: { event: ToolCallEvent }) {
  const command = String(event.args.command ?? event.args.cmd ?? event.tool ?? "");
  const output = event.result ?? "";
  const turns = useWorkspaceStore((s) => s.turns);
  const turn = turns.find((t) => t.events.some((e) => e.id === event.id));
  const shellEvt = turn?.events.find((e) => e.type === "shell") as ShellEvent | undefined;
  const finalOutput = shellEvt?.output || output;
  const exitCode = shellEvt?.exitCode ?? (event.status === "failed" ? 1 : undefined);
  const displayCmd = command || shellEvt?.command || "command";
  const isRunning = event.status === "running" && !(shellEvt && !shellEvt.isRunning);
  const hasFailed = event.status === "failed" || (exitCode != null && exitCode !== 0);

  const maxLines = 8;
  const outputLines = finalOutput.split("\n");
  const [expanded, setExpanded] = React.useState(false);
  const showOutput = expanded ? finalOutput : outputLines.length > maxLines ? outputLines.slice(0, maxLines).join("\n") + `\n… (${outputLines.length - maxLines} more)` : finalOutput;

  return (
    <div className="border border-border">
      <div className="flex items-center gap-1.5 px-2 py-1 bg-surface">
        {isRunning ? <Loader2 size={9} className="animate-spin text-accent" /> : hasFailed ? <X size={9} className="text-danger" /> : <Check size={9} className="text-success" />}
        <Terminal size={9} className="text-dim" />
        <span className="text-[11px] font-mono text-text truncate flex-1">{displayCmd}</span>
        {exitCode != null && <span className={cn("text-[9px] font-mono", exitCode === 0 ? "text-success" : "text-danger")}>exit {exitCode}</span>}
        {event.runtime != null && <span className="text-[9px] text-faint font-mono">{event.runtime.toFixed(1)}s</span>}
      </div>
      {(showOutput || isRunning) && (
        <pre
          onClick={() => outputLines.length > maxLines && setExpanded(!expanded)}
          className={cn("px-2 py-1 text-[10px] font-mono leading-relaxed whitespace-pre-wrap break-all overflow-y-auto bg-bg text-muted", outputLines.length > maxLines && "cursor-pointer")}
          style={{ maxHeight: expanded ? "300px" : "120px" }}
        >
          {showOutput || <span className="text-dim animate-pulse">Running…</span>}
        </pre>
      )}
    </div>
  );
}

/* ── Shell block ───────────────────────────────────────────── */
function ShellBlock({ event }: { event: ShellEvent }) {
  const ref = useRef<HTMLPreElement>(null);
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [event.output]);
  return (
    <div className="border border-border">
      <div className="flex items-center gap-1.5 px-2 py-1 bg-surface">
        {event.isRunning ? <Loader2 size={9} className="animate-spin text-accent" /> : event.exitCode === 0 ? <Check size={9} className="text-success" /> : <X size={9} className="text-danger" />}
        <Terminal size={9} className="text-dim" />
        <span className="text-[11px] font-mono text-text truncate flex-1">{event.command}</span>
        {event.runtime != null && <span className="text-[9px] text-faint font-mono">{event.runtime.toFixed(1)}s</span>}
        {!event.isRunning && event.exitCode != null && <span className={cn("text-[9px] font-mono", event.exitCode === 0 ? "text-success" : "text-danger")}>exit {event.exitCode}</span>}
      </div>
      {(event.output || event.isRunning) && (
        <pre ref={ref} className="px-2 py-1 text-[10px] font-mono leading-relaxed whitespace-pre-wrap break-all max-h-[120px] overflow-y-auto bg-bg text-muted">
          {event.output || <span className="text-dim animate-pulse">Waiting…</span>}
        </pre>
      )}
    </div>
  );
}

/* ── Diff block ────────────────────────────────────────────── */
function DiffBlock({ event, turnId }: { event: DiffEvent; turnId: string }) {
  const filename = event.path.replace(/\\/g, "/").split("/").pop() ?? event.path;
  const hasLines = event.hunks.some((h) => h.lines.length > 0);

  if (!hasLines) {
    return (
      <div className="flex items-center gap-1.5 px-2 py-1 border-l-2 border-success bg-success/[0.02]">
        <Check size={9} className="text-success shrink-0" />
        <FileCode size={9} className="text-dim" />
        <span className="text-[11px] font-mono text-text">{filename}</span>
        <span className="text-[9px] text-success ml-auto">Written</span>
      </div>
    );
  }

  return (
    <div className="border border-border">
      <div className="flex items-center gap-1.5 px-2 py-1 bg-panel border-b border-border">
        <Check size={9} className="text-success" />
        <FileCode size={9} className="text-dim" />
        <span className="text-[11px] font-mono text-text truncate">{filename}</span>
        <div className="flex-1" />
        <span className="text-[9px] text-success font-mono">+{event.additions}</span>
        {event.deletions > 0 && <span className="text-[9px] text-danger font-mono">-{event.deletions}</span>}
      </div>
      <div className="max-h-[240px] overflow-y-auto font-mono text-[10px] leading-relaxed">
        {event.hunks.map((hunk, hi) => (
          <div key={hi}>
            {hunk.header && <div className="px-2 py-0.5 bg-surface text-[9px] text-faint select-none">{hunk.header}</div>}
            {hunk.lines.map((line, li) => (
              <div key={li} className={cn("px-2 flex", line.type === "add" && "bg-success/[0.04] border-l-2 border-success", line.type === "del" && "bg-danger/[0.04] border-l-2 border-danger", line.type === "ctx" && "border-l-2 border-transparent")}>
                <span className="w-3 shrink-0 text-center text-faint select-none text-[9px]">{line.type === "add" ? "+" : line.type === "del" ? "−" : " "}</span>
                <span className={cn(line.type === "add" && "text-success/80", line.type === "del" && "text-danger/80", line.type === "ctx" && "text-dim")}>{line.content}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Compact tool line ─────────────────────────────────────── */
function CompactToolLine({ event }: { event: ToolCallEvent }) {
  const Icon = TOOL_ICONS[event.tool] ?? Play;
  const target = getToolTarget(event);
  const hasResult = !!event.result && event.status !== "running";
  const [show, setShow] = React.useState(false);

  if (event.tool === "read_file") {
    const filePath = String(event.args.path ?? event.args.file_path ?? "");
    const filename = filePath.replace(/\\/g, "/").split("/").pop() ?? "file";
    return (
      <div className="flex items-center gap-1.5 px-2 py-1">
        <StatusIcon status={event.status} />
        <FileCode size={9} className="text-dim" />
        <span className="text-[10px] text-dim">Read</span>
        <span className="text-[10px] font-mono text-text">{filename}</span>
        {event.runtime != null && <span className="text-[9px] text-faint font-mono ml-auto">{event.runtime.toFixed(1)}s</span>}
      </div>
    );
  }

  return (
    <div>
      <button onClick={() => hasResult && setShow(!show)} className={cn("w-full flex items-center gap-1.5 px-2 py-1", hasResult && "hover:bg-panel-hover cursor-pointer")}>
        <StatusIcon status={event.status} />
        <Icon size={9} className="text-dim" />
        <span className="text-[10px] text-dim font-mono">{event.tool}</span>
        {target && <span className="text-[10px] text-muted truncate max-w-[200px]">{target}</span>}
        {event.runtime != null && <span className="text-[9px] text-faint font-mono ml-auto">{event.runtime.toFixed(1)}s</span>}
      </button>
      {show && hasResult && (
        <pre className="border border-border px-2 py-1 text-[10px] font-mono leading-relaxed whitespace-pre-wrap break-all max-h-[160px] overflow-y-auto bg-bg text-dim ml-4">
          {event.result!.length > 1000 ? event.result!.slice(0, 1000) + "\n…" : event.result}
        </pre>
      )}
    </div>
  );
}

/* ── Text block ────────────────────────────────────────────── */
function TextBlock({ event }: { event: TextEvent }) {
  const content = event.content?.trim();
  if (event.isStreaming || !content || content.length < 10) return null;
  return (
    <div className="px-3 py-1">
      <div className="prose-kryth">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
      </div>
    </div>
  );
}

/* ── Final / Summary block ─────────────────────────────────── */
function FinalBlock({ event }: { event: { summary: string; filesModified: string[]; nextSteps: string[]; commands?: string[] } }) {
  const turns = useWorkspaceStore((s) => s.turns);
  const lastTurn = turns[turns.length - 1];
  const turnEvents = lastTurn?.events ?? [];

  const filesCreated: string[] = []; const filesModified: string[] = []; const filesDeleted: string[] = [];
  const commandsRun: { cmd: string; success: boolean }[] = [];
  let totalLinesWritten = 0;

  for (const evt of turnEvents) {
    if (evt.type === "tool_call") {
      const tc = evt as ToolCallEvent; const path = String(tc.args.path ?? tc.args.file_path ?? "");
      if (tc.tool === "create_file" && path) { filesCreated.push(path); totalLinesWritten += String(tc.args.content ?? "").split("\n").length; }
      else if (tc.tool === "write_file" && path) {
        if (!filesCreated.includes(path) && !filesModified.includes(path)) filesCreated.push(path);
        totalLinesWritten += String(tc.args.content ?? "").split("\n").length;
      } else if (["edit_file","multi_edit"].includes(tc.tool) && path && !filesModified.includes(path) && !filesCreated.includes(path)) filesModified.push(path);
      else if (tc.tool === "delete_file" && path) filesDeleted.push(path);
      else if (["run_command","execute"].includes(tc.tool)) { const cmd = String(tc.args.command ?? tc.args.cmd ?? ""); if (cmd) commandsRun.push({ cmd, success: tc.status === "success" }); }
    }
    if (evt.type === "diff") {
      const de = evt as DiffEvent;
      if (de.path && !filesCreated.includes(de.path) && !filesModified.includes(de.path)) {
        if (de.deletions === 0 && de.additions > 0) filesCreated.push(de.path); else filesModified.push(de.path);
      }
      totalLinesWritten += de.additions ?? 0;
    }
  }

  const allCreated = [...new Set(filesCreated)];
  const allModified = [...new Set([...filesModified, ...(event.filesModified ?? []).filter((f: string) => !filesCreated.includes(f))])];
  const totalFiles = allCreated.length + allModified.length + filesDeleted.length;
  const hasChanges = totalFiles > 0 || commandsRun.length > 0;

  return (
    <div className="border border-success/30 bg-success/[0.02]">
      <div className="px-3 py-2 flex items-start gap-2">
        <CheckCircle2 size={12} className="text-success shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-[11px] font-semibold text-success">Task Complete</span>
            {totalFiles > 0 && <span className="text-[9px] text-dim font-mono">{totalFiles} file{totalFiles > 1 ? "s" : ""}</span>}
            {totalLinesWritten > 0 && <span className="text-[9px] text-success/70 font-mono">+{totalLinesWritten} lines</span>}
          </div>
          <p className="text-[12px] text-text leading-relaxed">{event.summary}</p>
        </div>
      </div>

      {hasChanges && (
        <div className="mx-3 mb-2 border border-border">
          {allCreated.length > 0 && (
            <div className="border-b border-border/50">
              <div className="px-2 py-0.5 bg-success/[0.04] text-[9px] text-success font-semibold uppercase tracking-wider">Created ({allCreated.length})</div>
              <div className="px-2 py-1 space-y-0.5">
                {allCreated.map((f) => <FileRow key={f} path={f} type="created" />)}
              </div>
            </div>
          )}
          {allModified.length > 0 && (
            <div className="border-b border-border/50">
              <div className="px-2 py-0.5 bg-accent/[0.04] text-[9px] text-accent font-semibold uppercase tracking-wider">Modified ({allModified.length})</div>
              <div className="px-2 py-1 space-y-0.5">
                {allModified.map((f) => <FileRow key={f} path={f} type="modified" />)}
              </div>
            </div>
          )}
          {filesDeleted.length > 0 && (
            <div className="border-b border-border/50">
              <div className="px-2 py-0.5 bg-danger/[0.04] text-[9px] text-danger font-semibold uppercase tracking-wider">Deleted ({filesDeleted.length})</div>
              <div className="px-2 py-1 space-y-0.5">
                {filesDeleted.map((f) => <FileRow key={f} path={f} type="deleted" />)}
              </div>
            </div>
          )}
          {commandsRun.length > 0 && (
            <div>
              <div className="px-2 py-0.5 bg-surface text-[9px] text-dim font-semibold uppercase tracking-wider">Commands ({commandsRun.length})</div>
              <div className="px-2 py-1 space-y-0.5">
                {commandsRun.slice(0, 5).map((c, i) => (
                  <div key={i} className="flex items-center gap-1.5">
                    {c.success ? <Check size={8} className="text-success shrink-0" /> : <X size={8} className="text-danger shrink-0" />}
                    <code className="text-[10px] font-mono text-dim truncate">{c.cmd}</code>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {event.nextSteps?.length > 0 && (
        <div className="border-t border-border/30 px-3 py-1.5">
          <div className="flex items-center gap-1 mb-0.5">
            <Lightbulb size={8} className="text-warning" />
            <span className="text-[9px] text-dim font-semibold uppercase tracking-wider">Next Steps</span>
          </div>
          {event.nextSteps.map((s: string, i: number) => (
            <div key={i} className="flex items-start gap-1.5 py-0.5">
              <ArrowRight size={7} className="text-accent shrink-0 mt-[3px]" />
              <span className="text-[10px] text-muted">{s}</span>
            </div>
          ))}
        </div>
      )}

      {event.commands && event.commands.length > 0 && (
        <div className="border-t border-border/30 px-3 py-1.5">
          <div className="flex items-center gap-1 mb-0.5">
            <Terminal size={8} className="text-dim" />
            <span className="text-[9px] text-dim font-semibold uppercase tracking-wider">Try it</span>
          </div>
          {event.commands.map((c: string, i: number) => (
            <div key={i} className="px-2 py-0.5 bg-surface border border-border mt-0.5">
              <code className="text-[10px] font-mono text-text select-all">{c}</code>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function FileRow({ path, type }: { path: string; type: "created" | "modified" | "deleted" }) {
  const parts = path.replace(/\\/g, "/").split("/");
  const filename = parts.pop() ?? path;
  const dir = parts.length > 2 ? "…/" + parts.slice(-2).join("/") : parts.join("/");
  const handleClick = async () => {
    try { const content = await (await import("@/lib/krythBridge")).bridge.readFile(path); useEditorStore.getState().openTab({ path, filename, content, language: "" }); }
    catch { /* noop */ }
  };
  const colors = { created: "text-success", modified: "text-accent", deleted: "text-danger line-through" };
  return (
    <button onClick={handleClick} className="w-full flex items-center gap-1.5 py-0.5 hover:bg-panel-hover rounded px-1 -mx-1 transition-colors">
      <FileCode size={8} className={cn("shrink-0", colors[type])} />
      <span className={cn("text-[10px] font-mono truncate", colors[type], "hover:underline")}>{filename}</span>
      {dir && <span className="text-[8px] text-dim truncate ml-auto">{dir}</span>}
    </button>
  );
}

/* ── Agent update ──────────────────────────────────────────── */
function AgentBlock({ event }: { event: AgentEvent }) {
  return (
    <div className="flex items-center gap-1.5 px-2 py-1 border border-border bg-panel">
      <Bot size={9} className="text-accent shrink-0" />
      <span className="text-[11px] font-medium text-text">{event.name}</span>
      {event.role && <span className="text-[9px] text-dim px-1 bg-surface border border-border">{event.role}</span>}
      <span className="text-[10px] text-dim truncate flex-1">{event.task}</span>
      <StatusIcon status={event.status} />
    </div>
  );
}

/* ── Mission block ─────────────────────────────────────────── */
function MissionBlock({ event, turnId }: { event: MissionEvent; turnId: string }) {
  const toggle = useWorkspaceStore((s) => s.toggleMissionCollapse);
  const pct = Math.round(event.progress * 100);
  return (
    <div className="border border-border">
      <button onClick={() => toggle(turnId, event.id)} className="w-full flex items-center gap-1.5 px-2 py-1 hover:bg-panel-hover transition-colors text-left">
        {event.collapsed ? <ChevronRight size={9} className="text-dim" /> : <ChevronDown size={9} className="text-dim" />}
        <Rocket size={9} className="text-accent" />
        <span className="text-[11px] font-medium text-text">{event.title}</span>
        <div className="flex-1" />
        <span className="text-[9px] text-dim font-mono">{pct}%</span>
      </button>
      {!event.collapsed && (
        <div className="px-2 py-1 space-y-0.5 border-t border-border">
          {event.agents.map((a) => (
            <div key={a.id} className="flex items-center gap-1.5 py-0.5">
              {a.status === "done" && <Check size={8} className="text-success" />}
              {a.status === "running" && <Loader2 size={8} className="animate-spin text-accent" />}
              {a.status === "failed" && <X size={8} className="text-danger" />}
              {a.status === "queued" && <span className="w-[8px] h-[8px] border border-dim/30" />}
              <span className="text-[10px] text-text">{a.name}</span>
              <span className="text-[9px] text-dim truncate">{a.task}</span>
            </div>
          ))}
          {event.summary && <p className="text-[10px] text-dim mt-1 pt-1 border-t border-border">{event.summary}</p>}
        </div>
      )}
    </div>
  );
}

/* ── Reflection ────────────────────────────────────────────── */
function ReflectionBlock({ event }: { event: ReflectionEvent }) {
  const colors: Record<string, string> = { failure_analysis: "text-danger/70", success_pattern: "text-success/70", improvement: "text-accent/70" };
  return (
    <div className="flex items-start gap-1.5 px-2 py-1 border-l-2 border-accent/30 bg-panel">
      <Lightbulb size={8} className={cn("mt-0.5 shrink-0", colors[event.category])} />
      <p className="text-[10px] text-dim italic">{event.insight}</p>
    </div>
  );
}

/* ── Error block ───────────────────────────────────────────── */
function ErrorBlock({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex items-center gap-2 px-3 py-2 border border-danger/30 bg-danger/[0.02]">
      <AlertTriangle size={11} className="text-danger shrink-0" />
      <span className="text-[11px] text-text flex-1">Something went wrong</span>
      <button onClick={onRetry} className="flex items-center gap-1 px-1.5 py-0.5 text-[10px] text-danger border border-danger/30 hover:bg-danger/[0.05] transition-colors"><RotateCcw size={8} /> Retry</button>
    </div>
  );
}

/* ── Helpers ───────────────────────────────────────────────── */
function StatusIcon({ status }: { status: string }) {
  if (status === "running") return <Loader2 size={8} className="animate-spin text-accent shrink-0" />;
  if (status === "success") return <Check size={8} className="text-success shrink-0" />;
  if (status === "failed") return <X size={8} className="text-danger shrink-0" />;
  return <span className="w-[8px] h-[8px] border border-dim/30 shrink-0" />;
}

const TOOL_ICONS: Record<string, React.ElementType> = {
  write_file: FileCode, create_file: FileCode, read_file: FileCode,
  delete_file: FileCode, edit_file: FileCode,
  run_command: Terminal, execute: Terminal, shell: Terminal,
  search_code: Search, grep_search: Search, web_search: Globe,
};

function getToolTarget(tool: ToolCallEvent): string {
  const a = tool.args;
  const p = a.path ?? a.file_path ?? a.filepath;
  if (typeof p === "string") { const s = p.replace(/\\/g, "/").split("/"); return s.length > 2 ? `…/${s.slice(-2).join("/")}` : p; }
  const c = a.command ?? a.cmd;
  if (typeof c === "string") return c.length > 40 ? c.slice(0, 40) + "…" : c;
  const q = a.query ?? a.pattern;
  if (typeof q === "string") return `"${String(q).slice(0, 30)}"`;
  return "";
}

/* ── Pinned Plan ───────────────────────────────────────────── */
export function PinnedPlan() {
  const turns = useWorkspaceStore((s) => s.turns);
  const [collapsed, setCollapsed] = React.useState(false);

  let latestPlan: PlanEvent | null = null;
  for (const turn of turns) {
    for (const evt of turn.events) {
      if (evt.type === "plan") latestPlan = evt as PlanEvent;
    }
  }

  if (!latestPlan || latestPlan.steps.length === 0) return null;

  const done = latestPlan.steps.filter((s) => s.status === "done").length;
  const total = latestPlan.steps.length;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  const allDone = done === total;
  const activeStep = latestPlan.steps.find((s) => s.status === "active");

  if (allDone && total > 0) return null;

  return (
    <div className="border-t border-border bg-surface shrink-0">
      <button onClick={() => setCollapsed(!collapsed)} className="w-full flex items-center gap-1.5 px-3 py-1 hover:bg-panel-hover transition-colors text-left">
        {collapsed ? <ChevronRight size={9} className="text-dim" /> : <ChevronDown size={9} className="text-dim" />}
        <Rocket size={9} className="text-accent" />
        <span className="text-[10px] font-medium text-text">Plan</span>
        <div className="flex-1 h-1 bg-border-soft overflow-hidden mx-1">
          <div className="h-full bg-accent transition-all duration-500" style={{ width: `${pct}%` }} />
        </div>
        <span className="text-[9px] text-dim font-mono">{done}/{total}</span>
        {activeStep && <span className="text-[9px] text-accent truncate max-w-[120px]">{activeStep.label}</span>}
      </button>
      {!collapsed && (
        <div className="px-3 pb-1 max-h-[160px] overflow-y-auto space-y-0">
          {latestPlan.steps.map((step) => (
            <div key={step.id} className="flex items-center gap-1.5 py-0.5">
              {step.status === "done" && <Check size={8} className="text-success shrink-0" />}
              {step.status === "active" && <Loader2 size={8} className="animate-spin text-accent shrink-0" />}
              {step.status === "failed" && <X size={8} className="text-danger shrink-0" />}
              {step.status === "pending" && <span className="w-[8px] h-[8px] border border-dim/30 shrink-0" />}
              <span className={cn("text-[10px]", step.status === "done" && "text-dim line-through", step.status === "active" && "text-text font-medium", step.status === "pending" && "text-faint", step.status === "failed" && "text-danger")}>{step.label}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="h-full flex flex-col items-center justify-center gap-3 text-dim px-6">
      <div className="border border-border bg-panel p-3 flex items-center justify-center">
        <Sparkles size={18} className="text-text" />
      </div>
      <div className="text-center max-w-xs">
        <p className="text-xs font-medium text-text mb-1">Ready to build</p>
        <p className="text-[10px] text-dim leading-relaxed font-mono">Describe what you want to build, debug, or refactor.</p>
      </div>
    </div>
  );
}
