import React from "react";
import { usePipeline, useToolSummaries, useToolHistory, useStore } from "../hooks/useStore";
import type { PipelineStageName } from "../../runtime/types";

const STAGE_ICONS: Record<string, string> = {
  task: "◇",
  scan: "◎",
  plan: "△",
  execute: "▶",
  verify: "✓",
  respond: "◆",
  done: "★",
};

const STAGE_LABELS: Record<PipelineStageName, string> = {
  task: "Task",
  scan: "Scan",
  plan: "Plan",
  execute: "Execute",
  verify: "Verify",
  respond: "Done",
  done: "✓",
};

export function Inspector() {
  const store = useStore();
  const ui = store.ui;

  if (!ui.inspectorOpen) return null;

  return (
    <div className="kryth-inspector">
      <div className="kryth-inspector-header">Pipeline</div>
      <PipelinePanel />
      <div className="kryth-inspector-header">Tools</div>
      <ToolSummaryPanel />
      <ToolHistoryPanel />
    </div>
  );
}

function PipelinePanel() {
  const pipeline = usePipeline();

  return (
    <div className="kryth-pipeline">
      {pipeline.stages.map((stage) => (
        <div key={stage.name} className={`kryth-stage ${stage.state}`}>
          <span className="kryth-stage-icon">
            {stage.state === "running" ? "◐" : STAGE_ICONS[stage.name] || "◇"}
          </span>
          <span>{STAGE_LABELS[stage.name] || stage.name}</span>
          {stage.detail && (
            <span className="kryth-stage-detail">{stage.detail}</span>
          )}
        </div>
      ))}
    </div>
  );
}

function ToolSummaryPanel() {
  const summaries = useToolSummaries();

  if (summaries.length === 0) return null;

  return (
    <div className="kryth-tool-summary">
      {summaries.map((s, i) => (
        <div key={i} className="kryth-tool-summary-item">
          <span>{s.icon}</span>
          <span>{s.label}</span>
          {s.count > 1 && (
            <span style={{ color: "var(--text-dim)" }}>×{s.count}</span>
          )}
        </div>
      ))}
    </div>
  );
}

function ToolHistoryPanel() {
  const store = useStore();
  const history = useToolHistory();

  if (history.length === 0) return null;

  return (
    <div style={{ overflow: "auto", flex: 1 }}>
      {history
        .slice()
        .reverse()
        .slice(0, 20)
        .map((block) => (
          <div key={block.id} className="kryth-tool-block">
            <div
              className={`kryth-tool-block-header ${block.status}`}
              onClick={() => store.toggleToolExpanded(block.id)}
            >
              <span>{block.status === "success" ? "✓" : block.status === "failed" ? "✗" : "◇"}</span>
              <span>{block.label}</span>
              <span className="kryth-tool-block-args">{block.args}</span>
              <span style={{ color: "var(--text-dim)", fontSize: "var(--font-size-sm)" }}>
                {block.durationMs > 0 ? `${(block.durationMs / 1000).toFixed(1)}s` : ""}
              </span>
            </div>
            {block.expanded && (
              <>
                {block.output && (
                  <div className="kryth-tool-block-output">{block.output}</div>
                )}
                {block.affectedFiles.length > 0 && (
                  <div className="kryth-tool-block-files">
                    {block.affectedFiles.map((f, i) => (
                      <span key={i} className="kryth-file-chip">{f}</span>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        ))}
    </div>
  );
}
