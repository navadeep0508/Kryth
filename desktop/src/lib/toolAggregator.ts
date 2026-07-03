import type { ToolCallEvent } from "@/store/workspaceStore";

/** Semantic action label for a tool call group. */
export interface SemanticAction {
  id: string;
  label: string;
  icon: string;
  count: number;
  runtime: number;
  status: "running" | "done" | "failed" | "mixed";
  details: string;
}

/** Map from raw tool name → semantic grouping key. */
const SEMANTIC_GROUP: Record<string, string> = {
  grep_search: "scan",
  search_code: "scan",
  search_files: "scan",
  web_search: "scan",
  read_file: "read",
  read_files: "read",
  write_file: "write",
  create_file: "write",
  edit_file: "write",
  multi_edit: "write",
  delete_file: "write",
  run_command: "command",
  execute: "command",
  shell: "command",
  list_files: "browse",
  list_directory: "browse",
  todo_write: "plan",
  task_output: "plan",
  git_commit: "git",
  git_diff: "git",
  git_status: "git",
  subagent: "delegate",
  think: "reason",
  reasoning: "reason",
};

const GROUP_LABEL: Record<string, { label: string; icon: string }> = {
  scan:    { label: "Repository Scan",    icon: "search" },
  read:    { label: "Reading File",       icon: "file" },
  write:   { label: "Applying Changes",   icon: "edit" },
  command: { label: "Validation",         icon: "terminal" },
  browse:  { label: "Exploring",          icon: "folder" },
  plan:    { label: "Planning",           icon: "list" },
  git:     { label: "Version Control",    icon: "git" },
  delegate: { label: "Delegating",        icon: "bot" },
  reason:  { label: "Reasoning",          icon: "brain" },
};

const DEFAULT_GROUP = { label: "Tool", icon: "tool" };

/** Group raw tool calls into semantic actions, aggregating repeats. */
export function aggregateTools(tools: ToolCallEvent[]): SemanticAction[] {
  const groups = new Map<string, ToolCallEvent[]>();

  for (const tool of tools) {
    const groupKey = SEMANTIC_GROUP[tool.tool] ?? "tool";
    if (!groups.has(groupKey)) groups.set(groupKey, []);
    groups.get(groupKey)!.push(tool);
  }

  const result: SemanticAction[] = [];

  for (const [key, items] of groups) {
    const meta = GROUP_LABEL[key] ?? DEFAULT_GROUP;
    const running = items.some((t) => t.status === "running");
    const failed = items.some((t) => t.status === "failed");
    const succeeded = items.some((t) => t.status === "success");
    const totalRuntime = items.reduce((sum, t) => sum + (t.runtime ?? 0), 0);

    let status: SemanticAction["status"] = "running";
    if (!running) {
      if (failed && succeeded) status = "mixed";
      else if (failed) status = "failed";
      else status = "done";
    }

    const targets = new Set<string>();
    for (const t of items) {
      const target = t.args.path ?? t.args.file_path ?? t.args.command ?? t.args.query ?? "";
      if (typeof target === "string" && target) {
        const short = target.replace(/\\/g, "/").split("/").pop() ?? target;
        targets.add(short.length > 30 ? short.slice(0, 30) + "…" : short);
      }
    }

    const details = targets.size > 0
      ? [...targets].slice(0, 2).join(", ") + (targets.size > 2 ? ` +${targets.size - 2}` : "")
      : items.length > 1 ? `${items.length} calls` : "";

    result.push({
      id: key,
      label: meta.label,
      icon: meta.icon,
      count: items.length,
      runtime: totalRuntime,
      status,
      details,
    });
  }

  return result.sort((a, b) => {
    const order = ["scan", "read", "plan", "write", "command", "git", "browse", "delegate", "reason", "tool"];
    return order.indexOf(a.id) - order.indexOf(b.id);
  });
}

/** Get current running tool description. */
export function runningAction(tools: ToolCallEvent[]): { label: string; target: string } | null {
  const running = tools.filter((t) => t.status === "running");
  if (running.length === 0) return null;

  const first = running[0];
  const groupKey = SEMANTIC_GROUP[first.tool] ?? "tool";
  const meta = GROUP_LABEL[groupKey] ?? DEFAULT_GROUP;
  const target = first.args.path ?? first.args.file_path ?? first.args.command ?? first.args.query ?? "";
  const shortTarget = typeof target === "string" && target
    ? (target.replace(/\\/g, "/").split("/").pop() ?? target)
    : "";

  if (running.length > 1) {
    return { label: `${meta.label} (${running.length})`, target: shortTarget };
  }
  return { label: meta.label, target: shortTarget };
}

/** Count semantic action categories. */
export interface AggregatedSummary {
  scans: number;
  reads: number;
  writes: number;
  commands: number;
  total: number;
  runtime: number;
}

export function summarizeTools(tools: ToolCallEvent[]): AggregatedSummary {
  const groups = aggregateTools(tools);
  const summarize: AggregatedSummary = { scans: 0, reads: 0, writes: 0, commands: 0, total: tools.length, runtime: 0 };

  for (const g of groups) {
    switch (g.id) {
      case "scan": summarize.scans = g.count; break;
      case "read": summarize.reads = g.count; break;
      case "write": summarize.writes = g.count; break;
      case "command": summarize.commands = g.count; break;
    }
    summarize.runtime += g.runtime;
  }

  return summarize;
}
