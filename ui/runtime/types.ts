export type PipelineStageName =
  | "task"
  | "scan"
  | "plan"
  | "execute"
  | "verify"
  | "respond"
  | "done";

export type StageState = "pending" | "running" | "success" | "failed" | "warning";

export interface PipelineStage {
  name: PipelineStageName;
  label: string;
  state: StageState;
  elapsedMs: number;
  detail: string;
}

export interface PipelineState {
  stages: PipelineStage[];
  currentStage: PipelineStageName | null;
  totalElapsedMs: number;
}

export interface SessionInfo {
  provider: string;
  model: string;
  adapter: string;
  tokensIn: number;
  tokensOut: number;
  mode: string;
}

export interface ToolBlock {
  id: string;
  toolName: string;
  label: string;
  args: string;
  status: "running" | "success" | "failed" | "skipped";
  startedAt: number;
  durationMs: number;
  output: string;
  expanded: boolean;
  affectedFiles: string[];
}

export interface ToolSummary {
  label: string;
  count: number;
  icon: string;
}

export interface UISettings {
  sidebarOpen: boolean;
  inspectorOpen: boolean;
  sidebarWidth: number;
  inspectorWidth: number;
  theme: "dark" | "light";
  fontSize: number;
  fontFamily: string;
}

export const DEFAULT_UI_SETTINGS: UISettings = {
  sidebarOpen: true,
  inspectorOpen: false,
  sidebarWidth: 240,
  inspectorWidth: 300,
  theme: "dark",
  fontSize: 14,
  fontFamily: "Geist Mono, JetBrains Mono, monospace",
};

export type PaneId = "sidebar" | "main" | "inspector";

export interface PaneConfig {
  id: PaneId;
  width: number;
  minWidth: number;
  maxWidth: number;
  defaultWidth: number;
  visible: boolean;
  resizable: boolean;
}

export interface PaneDimensions {
  x: number;
  y: number;
  width: number;
  height: number;
}

export const DEFAULT_PIPELINE_STAGES: PipelineStage[] = [
  { name: "task", label: "Task", state: "pending", elapsedMs: 0, detail: "" },
  { name: "scan", label: "Scan", state: "pending", elapsedMs: 0, detail: "" },
  { name: "plan", label: "Plan", state: "pending", elapsedMs: 0, detail: "" },
  { name: "execute", label: "Execute", state: "pending", elapsedMs: 0, detail: "" },
  { name: "verify", label: "Verify", state: "pending", elapsedMs: 0, detail: "" },
  { name: "respond", label: "Done", state: "pending", elapsedMs: 0, detail: "" },
];
