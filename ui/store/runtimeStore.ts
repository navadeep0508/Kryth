import { TerminalBuffer } from "../runtime/terminal_buffer";
import { Viewport } from "../runtime/viewport";
import { Cursor } from "../runtime/cursor";
import type {
  PipelineState,
  PipelineStage,
  PipelineStageName,
  StageState,
  SessionInfo,
  ToolBlock,
  ToolSummary,
  UISettings,
} from "../runtime/types";
import { DEFAULT_UI_SETTINGS, DEFAULT_PIPELINE_STAGES } from "../runtime/types";

export type StoreListener = () => void;

export type StoreEvent =
  | "buffer:write"
  | "buffer:clear"
  | "buffer:resize"
  | "viewport:scroll"
  | "cursor:move"
  | "pipeline:stage"
  | "tool:start"
  | "tool:end"
  | "tool:summary"
  | "session:update"
  | "ui:layout"
  | "ui:theme";

export class RuntimeStore {
  // ── Terminal ──────────────────────────────────────
  buffer: TerminalBuffer;
  viewport: Viewport;
  cursor: Cursor;

  // ── Pipeline ───────────────────────────────────────
  pipeline: PipelineState = {
    stages: DEFAULT_PIPELINE_STAGES.map((s) => ({ ...s })),
    currentStage: null,
    totalElapsedMs: 0,
  };

  // ── Session ────────────────────────────────────────
  session: SessionInfo = {
    provider: "",
    model: "",
    adapter: "",
    tokensIn: 0,
    tokensOut: 0,
    mode: "normal",
  };

  // ── Tools ──────────────────────────────────────────
  activeTools: ToolBlock[] = [];
  toolHistory: ToolBlock[] = [];
  toolSummaries: ToolSummary[] = [];
  private _toolIdCounter = 0;

  // ── UI ─────────────────────────────────────────────
  ui: UISettings = { ...DEFAULT_UI_SETTINGS };

  // ── Subscribers ────────────────────────────────────
  private _listeners = new Map<StoreEvent, Set<StoreListener>>();

  constructor(rows = 30, cols = 100) {
    this.buffer = new TerminalBuffer(rows, cols);
    this.viewport = new Viewport(this.buffer);
    this.cursor = this.buffer.cursor;
  }

  // ── Subscription ───────────────────────────────────

  on(event: StoreEvent, fn: StoreListener): () => void {
    if (!this._listeners.has(event)) {
      this._listeners.set(event, new Set());
    }
    this._listeners.get(event)!.add(fn);
    return () => this._listeners.get(event)?.delete(fn);
  }

  protected _emit(event: StoreEvent): void {
    this._listeners.get(event)?.forEach((fn) => {
      try {
        fn();
      } catch {
        /* subscriber error is non-fatal */
      }
    });
  }

  // ── Buffer Operations ──────────────────────────────

  write(text: string, style?: Record<string, unknown>): void {
    this.buffer.write(text, style as any);
    this._emit("buffer:write");
  }

  writeln(text: string, style?: Record<string, unknown>): void {
    this.buffer.writeln(text, style as any);
    this._emit("buffer:write");
  }

  clearBuffer(): void {
    this.buffer.clear();
    this._emit("buffer:clear");
  }

  resizeBuffer(rows: number, cols: number): void {
    this.buffer.resize(rows, cols);
    this.viewport.scrollToBottom();
    this._emit("buffer:resize");
  }

  // ── Viewport Operations ────────────────────────────

  scrollToTop(): void {
    this.viewport.scrollToTop();
    this._emit("viewport:scroll");
  }

  scrollToBottom(): void {
    this.viewport.scrollToBottom();
    this._emit("viewport:scroll");
  }

  scrollUp(n = 1): void {
    this.viewport.scrollUp(n);
    this._emit("viewport:scroll");
  }

  scrollDown(n = 1): void {
    this.viewport.scrollDown(n);
    this._emit("viewport:scroll");
  }

  // ── Pipeline Operations ────────────────────────────

  setStageState(name: PipelineStageName, state: StageState, detail = ""): void {
    const stage = this.pipeline.stages.find((s) => s.name === name);
    if (!stage) return;
    stage.state = state;
    stage.detail = detail;
    if (state === "running") {
      this.pipeline.currentStage = name;
    }
    this._emit("pipeline:stage");
  }

  setStageElapsed(name: PipelineStageName, ms: number): void {
    const stage = this.pipeline.stages.find((s) => s.name === name);
    if (!stage) return;
    stage.elapsedMs = ms;
  }

  resetPipeline(): void {
    this.pipeline.stages = DEFAULT_PIPELINE_STAGES.map((s) => ({ ...s }));
    this.pipeline.currentStage = null;
    this.pipeline.totalElapsedMs = 0;
    this._emit("pipeline:stage");
  }

  // ── Tool Operations ────────────────────────────────

  startTool(toolName: string, label: string, args = ""): string {
    const id = `tool_${++this._toolIdCounter}`;
    const block: ToolBlock = {
      id,
      toolName,
      label,
      args,
      status: "running",
      startedAt: performance.now(),
      durationMs: 0,
      output: "",
      expanded: false,
      affectedFiles: [],
    };
    this.activeTools.push(block);
    this._emit("tool:start");
    return id;
  }

  endTool(id: string, status: ToolBlock["status"], output = ""): void {
    const idx = this.activeTools.findIndex((t) => t.id === id);
    if (idx < 0) return;
    const block = this.activeTools[idx];
    block.status = status;
    block.durationMs = performance.now() - block.startedAt;
    block.output = output.slice(0, 2000);
    this.activeTools.splice(idx, 1);
    this.toolHistory.push(block);
    if (this.toolHistory.length > 200) {
      this.toolHistory.splice(0, this.toolHistory.length - 200);
    }
    this._emit("tool:end");
  }

  addToolSummary(label: string, count = 1, icon = "◇"): void {
    const existing = this.toolSummaries.find((s) => s.label === label);
    if (existing) {
      existing.count += count;
    } else {
      this.toolSummaries.push({ label, count, icon });
    }
    this._emit("tool:summary");
  }

  toggleToolExpanded(id: string): void {
    const block = this.toolHistory.find((t) => t.id === id);
    if (block) {
      block.expanded = !block.expanded;
      this._emit("tool:end");
    }
  }

  clearToolHistory(): void {
    this.toolHistory = [];
    this.toolSummaries = [];
    this._emit("tool:summary");
  }

  // ── Session Operations ─────────────────────────────

  updateSession(info: Partial<SessionInfo>): void {
    Object.assign(this.session, info);
    this._emit("session:update");
  }

  addTokens(inTok: number, outTok: number): void {
    this.session.tokensIn += inTok;
    this.session.tokensOut += outTok;
    this._emit("session:update");
  }

  // ── UI Operations ──────────────────────────────────

  toggleSidebar(): void {
    this.ui.sidebarOpen = !this.ui.sidebarOpen;
    this._emit("ui:layout");
  }

  toggleInspector(): void {
    this.ui.inspectorOpen = !this.ui.inspectorOpen;
    this._emit("ui:layout");
  }

  setSidebarWidth(width: number): void {
    this.ui.sidebarWidth = Math.max(180, Math.min(400, width));
    this._emit("ui:layout");
  }

  setInspectorWidth(width: number): void {
    this.ui.inspectorWidth = Math.max(240, Math.min(500, width));
    this._emit("ui:layout");
  }

  setTheme(theme: "dark" | "light"): void {
    this.ui.theme = theme;
    this._emit("ui:theme");
  }

  // ── Snapshot ───────────────────────────────────────

  snapshot() {
    return {
      terminal: {
        rows: this.buffer.rows,
        cols: this.buffer.cols,
        cursor: this.cursor.snapshot(),
        viewport: this.viewport.snapshot(),
      },
      pipeline: { ...this.pipeline },
      session: { ...this.session },
      activeTools: [...this.activeTools],
      toolHistory: this.toolHistory.slice(-10),
      toolSummaries: [...this.toolSummaries],
      ui: { ...this.ui },
    };
  }
}

let _globalStore: RuntimeStore | null = null;

export function getStore(rows?: number, cols?: number): RuntimeStore {
  if (!_globalStore) {
    _globalStore = new RuntimeStore(rows, cols);
  }
  return _globalStore;
}
