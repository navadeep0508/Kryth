import React, { memo, useEffect, useState } from "react";
import { Check, Key, Cpu, Zap, Eye, Brain, Sparkles, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { getAgentConfig, setAgentConfig, loadConfigFromBackend, type AgentConfig } from "@/lib/agentRuntime";
import { useUIStore } from "@/store/uiStore";
import { bridge } from "@/lib/krythBridge";

const PROVIDERS = [
  { id: "openai", name: "OpenAI", baseUrl: "https://api.openai.com/v1", placeholder: "sk-..." },
  { id: "anthropic", name: "Anthropic", baseUrl: "https://api.anthropic.com/v1", placeholder: "sk-ant-..." },
  { id: "openrouter", name: "OpenRouter", baseUrl: "https://openrouter.ai/api/v1", placeholder: "sk-or-..." },
  { id: "google", name: "Google AI", baseUrl: "https://generativelanguage.googleapis.com/v1beta", placeholder: "AIza..." },
  { id: "nvidia", name: "NVIDIA NIM", baseUrl: "https://integrate.api.nvidia.com/v1", placeholder: "nvapi-..." },
  { id: "local", name: "Local (Ollama)", baseUrl: "http://localhost:11434/v1", placeholder: "not needed" },
  { id: "custom", name: "Custom", baseUrl: "", placeholder: "your-api-key" },
] as const;

const POPULAR_MODELS: Record<string, string[]> = {
  openai: ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1-mini"],
  anthropic: ["claude-sonnet-4-20250514", "claude-3-5-sonnet-20241022", "claude-3-haiku-20240307"],
  openrouter: ["anthropic/claude-sonnet-4", "openai/gpt-4o", "google/gemini-2.0-flash-exp", "meta-llama/llama-3.1-70b"],
  google: ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
  nvidia: ["stepfun-ai/step-3.5-flash", "meta/llama-3.1-70b-instruct"],
  local: ["llama3.1", "codellama", "deepseek-coder-v2", "qwen2.5-coder"],
  custom: [],
};

export default memo(function SettingsPage() {
  const [config, setConfig] = useState<AgentConfig>(getAgentConfig());
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => { loadConfigFromBackend(); }, []);

  const save = (key: keyof AgentConfig, value: string | number | boolean) => {
    const updated = { ...config, [key]: value };
    setConfig(updated);
    setAgentConfig({ [key]: value } as any);
    if (key === "model" || key === "mainModel") {
      useUIStore.getState().setCurrentModel(String(value));
    }
    setSaved(key);
    setTimeout(() => setSaved(null), 1200);
  };

  const switchProvider = (id: string) => {
    const prov = PROVIDERS.find((p) => p.id === id);
    if (!prov) return;
    save("provider", id as any);
    save("baseUrl", prov.baseUrl);
    // Set a default model for the provider
    const models = POPULAR_MODELS[id] || [];
    if (models.length > 0) save("model", models[0]);
  };

  const providerModels = POPULAR_MODELS[config.provider] || [];

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[600px] mx-auto px-6 py-8 space-y-8">

        {/* Header */}
        <div>
          <h1 className="text-lg font-semibold text-text">Model Configuration</h1>
          <p className="text-xs text-dim mt-1">Configure which LLM models KRYTH uses for coding tasks.</p>
        </div>

        {/* Provider Selection */}
        <Section title="Provider">
          <div className="grid grid-cols-4 gap-2">
            {PROVIDERS.map((p) => (
              <button
                key={p.id}
                onClick={() => switchProvider(p.id)}
                className={cn(
                  "px-3 py-2 rounded-lg border text-xs font-medium transition-all",
                  config.provider === p.id
                    ? "border-accent bg-accent/5 text-accent"
                    : "border-border-soft text-muted hover:border-border-bright hover:text-text"
                )}
              >
                {p.name}
              </button>
            ))}
          </div>
        </Section>

        {/* API Key */}
        <Section title="API Key">
          <KeyInput
            value={config.apiKey}
            placeholder={PROVIDERS.find((p) => p.id === config.provider)?.placeholder || ""}
            onChange={(v) => { setConfig({ ...config, apiKey: v }); }}
            onBlur={() => save("apiKey", config.apiKey)}
            saved={saved === "apiKey"}
          />
        </Section>

        {/* Base URL — always visible and editable */}
        <Section title="Base URL">
          <div className="relative">
            <input
              type="text"
              value={config.baseUrl}
              onChange={(e) => setConfig({ ...config, baseUrl: e.target.value })}
              onBlur={() => save("baseUrl", config.baseUrl)}
              placeholder="https://api.openai.com/v1"
              className="w-full px-3 py-2.5 rounded-md border border-border bg-bg text-sm font-mono text-text placeholder:text-dim outline-none focus:border-accent"
            />
            {saved === "baseUrl" && <div className="absolute right-2.5 top-1/2 -translate-y-1/2"><Check size={11} className="text-success" /></div>}
          </div>
          <p className="text-[10px] text-dim mt-1.5">OpenAI-compatible endpoint. Change this for custom/self-hosted providers.</p>
        </Section>

        {/* Mode Toggle */}
        <div className="flex items-center justify-between p-4 rounded-xl border border-border-soft bg-panel/30">
          <div>
            <p className="text-sm font-medium text-text">Advanced Model Routing</p>
            <p className="text-[11px] text-dim mt-0.5">Use different models for coding, planning, summarizing, and vision</p>
          </div>
          <button
            onClick={() => save("advancedMode", !config.advancedMode)}
            className={cn(
              "w-11 h-6 rounded-full transition-colors relative",
              config.advancedMode ? "bg-accent" : "bg-border"
            )}
          >
            <div className={cn(
              "w-5 h-5 rounded-full bg-white shadow-sm absolute top-0.5 transition-transform",
              config.advancedMode ? "translate-x-[22px]" : "translate-x-0.5"
            )} />
          </button>
        </div>

        {/* Simple Mode — one model */}
        {!config.advancedMode && (
          <Section title="Model">
            <ModelSelect
              value={config.model}
              options={providerModels}
              onChange={(v) => save("model", v)}
              saved={saved === "model"}
              placeholder="Enter model ID..."
            />
          </Section>
        )}

        {/* Advanced Mode — per-role models */}
        {config.advancedMode && (
          <div className="space-y-4">
            <ModelRole
              icon={Cpu} label="Main (Coding)" hint="Primary model for tool calls and code generation"
              value={config.mainModel} options={providerModels}
              onChange={(v) => save("mainModel", v)} saved={saved === "mainModel"}
            />
            <ModelRole
              icon={Brain} label="Planner" hint="Used for task decomposition and planning (can be smaller/cheaper)"
              value={config.plannerModel} options={providerModels}
              onChange={(v) => save("plannerModel", v)} saved={saved === "plannerModel"}
            />
            <ModelRole
              icon={Sparkles} label="Summarizer" hint="Compresses conversation history (cheap model recommended)"
              value={config.summarizerModel} options={providerModels}
              onChange={(v) => save("summarizerModel", v)} saved={saved === "summarizerModel"}
            />
            <ModelRole
              icon={Eye} label="Vision" hint="For browser automation and image understanding (needs multimodal)"
              value={config.visionModel} options={providerModels.filter(m => !m.includes("mini") && !m.includes("haiku"))}
              onChange={(v) => save("visionModel", v)} saved={saved === "visionModel"}
            />
          </div>
        )}

        {/* Max Turns */}
        <Section title="Execution">
          <div className="flex items-center gap-4">
            <label className="text-xs text-muted w-24">Max turns</label>
            <input
              type="number"
              min={1} max={100}
              value={config.maxTurns}
              onChange={(e) => setConfig({ ...config, maxTurns: parseInt(e.target.value) || 30 })}
              onBlur={() => save("maxTurns", config.maxTurns)}
              className="w-20 px-3 py-1.5 rounded-md border border-border bg-bg text-sm text-text outline-none focus:border-accent"
            />
            <span className="text-[10px] text-dim">Tool iterations per task</span>
          </div>
        </Section>

        {/* Status */}
        <div className="text-[10px] text-dim text-center pb-4">
          Changes auto-save and apply immediately. No restart needed.
        </div>

      </div>
    </div>
  );
});

/* ─── Components ─── */

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h2 className="text-xs font-semibold text-dim uppercase tracking-wide mb-2.5">{title}</h2>
      {children}
    </div>
  );
}

function ModelRole({ icon: Icon, label, hint, value, options, onChange, saved }: {
  icon: React.ElementType; label: string; hint: string;
  value: string; options: string[]; onChange: (v: string) => void; saved: boolean;
}) {
  return (
    <div className="flex items-start gap-3 p-3 rounded-lg border border-border-soft bg-panel/20">
      <div className="w-8 h-8 rounded-md bg-accent/8 border border-accent/15 flex items-center justify-center shrink-0 mt-0.5">
        <Icon size={14} className="text-accent" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-text">{label}</span>
          {saved && <Check size={11} className="text-success" />}
        </div>
        <p className="text-[10px] text-dim mt-0.5 mb-2">{hint}</p>
        <ModelSelect value={value} options={options} onChange={onChange} saved={false} placeholder={`${label} model...`} />
      </div>
    </div>
  );
}

function ModelSelect({ value, options, onChange, saved, placeholder }: {
  value: string; options: string[]; onChange: (v: string) => void; saved: boolean; placeholder: string;
}) {
  const [open, setOpen] = useState(false);
  const [fetched, setFetched] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const lastFetchKey = React.useRef("");
  const containerRef = React.useRef<HTMLDivElement>(null);

  // Close on click outside
  React.useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const fetchModels = async () => {
    const cfg = getAgentConfig();
    const fetchKey = `${cfg.baseUrl}|${cfg.apiKey?.slice(0, 8)}`;
    if (fetchKey === lastFetchKey.current && fetched.length > 0) return;

    setLoading(true);
    setError("");
    try {
      // Send both baseUrl and key — backend will also check env vars
      const result = await bridge.listModels(cfg.baseUrl, cfg.apiKey);
      if (result.models && result.models.length > 0) {
        setFetched(result.models);
        lastFetchKey.current = fetchKey;
      } else if (result.error) {
        setError(result.error);
        setFetched([]);
        // Retry without key (some endpoints are public)
        const retry = await bridge.listModels(cfg.baseUrl, "");
        if (retry.models && retry.models.length > 0) {
          setFetched(retry.models);
          lastFetchKey.current = fetchKey;
          setError("");
        }
      } else {
        setError("No models returned");
        setFetched([]);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch models");
      setFetched([]);
    }
    setLoading(false);
  };

  const displayOptions = fetched.length > 0 ? fetched : options;

  return (
    <div className="relative" ref={containerRef}>
      <div className="flex gap-2">
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className="flex-1 px-3 py-2 rounded-md border border-border bg-bg text-sm font-mono text-text outline-none focus:border-accent"
        />
        <button
          onClick={() => {
            const next = !open;
            setOpen(next);
            if (next) fetchModels();
          }}
          className={cn(
            "px-2.5 py-2 rounded-md border transition-colors",
            open ? "border-accent text-accent bg-accent/5" : "border-border text-dim hover:text-accent hover:border-accent/30"
          )}
          title="Fetch models from API"
        >
          {loading ? <div className="w-3 h-3 border-2 border-dim border-t-accent rounded-full animate-spin" /> : <ChevronDown size={12} className={open ? "rotate-180 transition-transform" : "transition-transform"} />}
        </button>
        {saved && <div className="flex items-center"><Check size={12} className="text-success" /></div>}
      </div>
      {error && open && <p className="text-[10px] text-warning mt-1">{error}. You can type any model ID manually.</p>}
      {open && displayOptions.length > 0 && (
        <div className="absolute top-full left-0 right-0 mt-1 z-50 bg-bg border border-border-soft rounded-lg shadow-xl max-h-[300px] overflow-y-auto">
          {fetched.length > 0 && (
            <div className="sticky top-0 px-3 py-1.5 text-[9px] text-dim uppercase tracking-wide border-b border-border-soft bg-panel backdrop-blur-sm">
              {fetched.length} models from API
            </div>
          )}
          {displayOptions.map((m) => (
            <button
              key={m}
              onClick={() => { onChange(m); setOpen(false); }}
              className={cn(
                "w-full text-left px-3 py-1.5 text-xs font-mono hover:bg-panel-hover transition-colors",
                m === value ? "text-accent bg-accent/5" : "text-muted"
              )}
            >
              {m}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function KeyInput({ value, placeholder, onChange, onBlur, saved }: {
  value: string; placeholder: string; onChange: (v: string) => void; onBlur: () => void; saved: boolean;
}) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <input
        type={show ? "text" : "password"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
        placeholder={placeholder}
        className="w-full px-3 py-2.5 rounded-md border border-border bg-bg text-sm font-mono text-text placeholder:text-dim outline-none focus:border-accent pr-24"
      />
      <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-2">
        <button onClick={() => setShow(!show)} className="text-[10px] text-dim hover:text-muted px-1.5 py-0.5 rounded border border-transparent hover:border-border-soft">
          {show ? "hide" : "show"}
        </button>
        {saved && <Check size={11} className="text-success" />}
        {value && !saved && <div className="w-2 h-2 rounded-full bg-success" title="Key set" />}
      </div>
    </div>
  );
}
