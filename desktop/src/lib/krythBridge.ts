const BASE = "http://127.0.0.1:7765";
const WS_BASE = "ws://127.0.0.1:7765";

export async function request<T>(
  method: string,
  path: string,
  body?: unknown
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? res.statusText);
  }
  return res.json() as Promise<T>;
}

// ── Tauri invoke helper (works without Python server) ──────────────────────

async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const { invoke: tauriInvoke } = await import("@tauri-apps/api/core");
  return tauriInvoke<T>(cmd, args);
}

let _useTauri: boolean | null = null;

async function canUseTauri(): Promise<boolean> {
  if (_useTauri !== null) return _useTauri;
  try {
    const { invoke: tauriInvoke } = await import("@tauri-apps/api/core");
    await tauriInvoke("get_platform");
    _useTauri = true;
  } catch {
    _useTauri = false;
  }
  return _useTauri;
}

// ── Bridge: Tauri native first, HTTP fallback ──────────────────────────────

export const bridge = {
  wsUrl: `${WS_BASE}/ws/events`,

  health: async (): Promise<{ ok: boolean }> => {
    try {
      return await request<{ ok: boolean }>("GET", "/health");
    } catch {
      if (await canUseTauri()) return { ok: true };
      throw new Error("Backend unavailable");
    }
  },

  // Agent execution requires the Python runtime server
  runAgent: async (user_input: string, cwd = "") => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const res = await fetch(`${BASE}/api/agent/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_input, cwd }),
        signal: controller.signal,
      });
      clearTimeout(timeout);
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? res.statusText);
      }
      return res.json() as Promise<{ ok: boolean }>;
    } catch (e) {
      clearTimeout(timeout);
      // Re-throw errors that already have a meaningful message (from !res.ok above)
      if (e instanceof Error && e.message && !e.message.includes("fetch")) {
        throw e;
      }
      if (e instanceof Error && e.name === "AbortError") {
        throw new Error("Backend not responding (timeout). Is `python -m kryth.desktop_main` running?");
      }
      throw new Error("Backend unavailable. Start with: python -m kryth.desktop_main");
    }
  },

  stopAgent: () => request<{ ok: boolean }>("POST", "/api/agent/stop"),

  approve: (id: string, approved: boolean, always?: boolean) =>
    request<{ ok: boolean }>("POST", "/api/approve", { id, approved, always: always ?? false }),

  getConfig: () => request<Record<string, string>>("GET", "/api/config"),

  patchConfig: (key: string, value: string) =>
    request<{ ok: boolean }>("PATCH", "/api/config", { key, value }),

  listModels: (baseUrl?: string, apiKey?: string) =>
    request<{ models: string[]; error?: string }>(
      "GET",
      `/api/models?base_url=${encodeURIComponent(baseUrl || "")}&api_key=${encodeURIComponent(apiKey || "")}`
    ),

  // ── File operations — Tauri native (no Python server needed) ───────────

  listFiles: async (path = "."): Promise<FileEntry[]> => {
    if (await canUseTauri()) {
      return invoke<FileEntry[]>("list_files", { path });
    }
    return request<{ path: string; entries: FileEntry[] }>(
      "GET",
      `/api/files?path=${encodeURIComponent(path)}`
    ).then((r) => r.entries);
  },

  readFile: async (path: string): Promise<string> => {
    if (await canUseTauri()) {
      return invoke<string>("read_file", { path });
    }
    return request<{ path: string; content: string }>(
      "GET",
      `/api/file?path=${encodeURIComponent(path)}`
    ).then((r) => r.content);
  },

  writeFile: async (path: string, content: string): Promise<{ ok: boolean }> => {
    if (await canUseTauri()) {
      await invoke<null>("write_file", { path, content });
      return { ok: true };
    }
    return request<{ ok: boolean }>("POST", "/api/file", { path, content });
  },

  // ── Search — Tauri native ──────────────────────────────────────────────

  searchFiles: async (dir: string, pattern: string): Promise<string[]> => {
    if (await canUseTauri()) {
      return invoke<string[]>("search_files", { dir, pattern });
    }
    return [];
  },

  grepFiles: async (dir: string, pattern: string, maxResults = 50): Promise<string[]> => {
    if (await canUseTauri()) {
      return invoke<string[]>("grep_files", { dir, pattern, maxResults });
    }
    return [];
  },

  // ── Shell — Tauri native ───────────────────────────────────────────────

  runShell: async (command: string, cwd = ""): Promise<string> => {
    if (await canUseTauri()) {
      return invoke<string>("run_shell", { command, cwd });
    }
    throw new Error("Shell not available without Tauri");
  },

  // ── Session/tools/memory — HTTP (need Python runtime) ──────────────────

  getSessions: () =>
    request<{ sessions: Array<{ id: string; project_path: string; updated_at: string }> }>(
      "GET",
      "/api/sessions"
    ),

  getSessionHistory: (sessionId: string) =>
    request<{ session_id: string; events: Array<Record<string, unknown>> }>(
      "GET",
      `/api/sessions/${encodeURIComponent(sessionId)}/history`
    ),

  getTools: () =>
    request<{ tools: Array<{ name: string; description: string; source: "builtin" | "mcp" }> }>(
      "GET",
      "/api/tools"
    ),

  getMemory: () =>
    request<{ entries: Array<{ id: string; content: string; source: string; ts: string }> }>(
      "GET",
      "/api/memory"
    ),

  deleteMemory: (entryId: string) =>
    request<{ ok: boolean }>("DELETE", `/api/memory/${encodeURIComponent(entryId)}`),

  getLogs: (limit = 100) =>
    request<{ lines: string[] }>("GET", `/api/logs?limit=${limit}`),
};

export interface FileEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
}
