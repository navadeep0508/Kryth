/**
 * KRYTH Desktop — Agent Configuration
 *
 * Two modes:
 * 1. Simple: one model for everything (default)
 * 2. Advanced: separate models for main/planner/summarizer/vision
 *
 * Syncs to Python backend via /api/config.
 */

import { bridge } from "./krythBridge";

const CONFIG_KEY = "kryth:agent-config";

export interface AgentConfig {
  // Provider
  provider: "openai" | "anthropic" | "google" | "nvidia" | "openrouter" | "local" | "custom";
  apiKey: string;
  baseUrl: string;

  // Simple mode: one model for all
  model: string;

  // Advanced mode: per-role models
  advancedMode: boolean;
  mainModel: string;
  plannerModel: string;
  summarizerModel: string;
  visionModel: string;

  // Settings
  maxTurns: number;
}

const DEFAULTS: AgentConfig = {
  provider: "openai",
  apiKey: "",
  baseUrl: "https://api.openai.com/v1",
  model: "gpt-4o-mini",
  advancedMode: false,
  mainModel: "gpt-4o",
  plannerModel: "gpt-4o-mini",
  summarizerModel: "gpt-4o-mini",
  visionModel: "gpt-4o",
  maxTurns: 30,
};

// Backend env var mapping
const SYNC_MAP: Record<string, string> = {
  baseUrl: "KRYTH_BASE_URL",
  model: "KRYTH_MAIN_MODEL",
  mainModel: "KRYTH_MAIN_MODEL",
  plannerModel: "KRYTH_PLANNER_MODEL",
  summarizerModel: "KRYTH_SUMMARIZER_MODEL",
  visionModel: "KRYTH_VISION_MODEL",
};

// Map provider to the correct API key env var
const PROVIDER_KEY_MAP: Record<string, string> = {
  openai: "OPENAI_API_KEY",
  anthropic: "ANTHROPIC_API_KEY",
  google: "GOOGLE_API_KEY",
  nvidia: "NVIDIA_API_KEY",
  openrouter: "OPENAI_API_KEY",   // OpenRouter uses OpenAI-compatible key header
  local: "OPENAI_API_KEY",
  custom: "OPENAI_API_KEY",
};

export function getAgentConfig(): AgentConfig {
  try {
    const stored = localStorage.getItem(CONFIG_KEY);
    if (stored) return { ...DEFAULTS, ...JSON.parse(stored) };
  } catch {}
  return { ...DEFAULTS };
}

export function setAgentConfig(cfg: Partial<AgentConfig>): void {
  const current = getAgentConfig();
  const updated = { ...current, ...cfg };
  localStorage.setItem(CONFIG_KEY, JSON.stringify(updated));

  // Always sync the full active config to backend on any change
  // This ensures base_url + key + model are always in sync
  bridge.patchConfig("KRYTH_BASE_URL", updated.baseUrl).catch(() => {});
  
  // Sync the correct model based on mode
  if (updated.advancedMode) {
    bridge.patchConfig("KRYTH_MAIN_MODEL", updated.mainModel).catch(() => {});
    bridge.patchConfig("KRYTH_PLANNER_MODEL", updated.plannerModel).catch(() => {});
    bridge.patchConfig("KRYTH_SUMMARIZER_MODEL", updated.summarizerModel).catch(() => {});
    bridge.patchConfig("KRYTH_VISION_MODEL", updated.visionModel).catch(() => {});
  } else {
    bridge.patchConfig("KRYTH_MAIN_MODEL", updated.model).catch(() => {});
  }

  // Sync API key to the correct env var for the provider
  if (updated.apiKey) {
    const envKey = PROVIDER_KEY_MAP[updated.provider] || "OPENAI_API_KEY";
    bridge.patchConfig(envKey, updated.apiKey).catch(() => {});
    // Also set as OPENAI_API_KEY since that's what the client reads first
    if (envKey !== "OPENAI_API_KEY") {
      bridge.patchConfig("OPENAI_API_KEY", updated.apiKey).catch(() => {});
    }
  }
}

export async function loadConfigFromBackend(): Promise<void> {
  try {
    const bc = await bridge.getConfig();
    const updates: Partial<AgentConfig> = {};
    if (bc.KRYTH_MAIN_MODEL) updates.model = bc.KRYTH_MAIN_MODEL;
    if (bc.KRYTH_BASE_URL) updates.baseUrl = bc.KRYTH_BASE_URL;
    if (bc.KRYTH_PLANNER_MODEL) updates.plannerModel = bc.KRYTH_PLANNER_MODEL;
    if (bc.KRYTH_VISION_MODEL) updates.visionModel = bc.KRYTH_VISION_MODEL;
    if (Object.keys(updates).length > 0) {
      const current = getAgentConfig();
      localStorage.setItem(CONFIG_KEY, JSON.stringify({ ...current, ...updates }));
    }
  } catch {}
}

