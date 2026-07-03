import React, { memo, useEffect, useState, useCallback } from "react";
import { Globe, ExternalLink, ArrowLeft, ArrowRight, RotateCw, Monitor } from "lucide-react";

/**
 * Browser Panel — opens a real Tauri WebView window showing the agent's browser.
 * When the agent navigates to a URL, it opens/navigates the embedded browser window.
 */
export default memo(function BrowserPanel() {
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [action, setAction] = useState("");
  const [addressBar, setAddressBar] = useState("");
  const [windowOpen, setWindowOpen] = useState(false);

  // Listen for browser state from backend
  useEffect(() => {
    const wsUrl = "ws://127.0.0.1:7765/ws/browser";
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout>;

    function connect() {
      try {
        ws = new WebSocket(wsUrl);
        ws.onclose = () => { timer = setTimeout(connect, 5000); };
        ws.onerror = () => ws?.close();
        ws.onmessage = (evt) => {
          try {
            const msg = JSON.parse(evt.data);
            if (msg.type === "state" && msg.url) {
              setUrl(msg.url);
              setTitle(msg.title || "");
              setAddressBar(msg.url);
              // Auto-open Tauri browser window when URL changes
              openInTauri(msg.url);
            }
            if (msg.type === "action") {
              setAction(msg.label || "");
              setTimeout(() => setAction(""), 4000);
            }
            if (msg.type === "navigation" && msg.url) {
              setUrl(msg.url);
              setAddressBar(msg.url);
              openInTauri(msg.url);
            }
          } catch {}
        };
      } catch { timer = setTimeout(connect, 5000); }
    }

    connect();
    return () => { ws?.close(); clearTimeout(timer); };
  }, []);

  const openInTauri = useCallback(async (targetUrl: string) => {
    if (!targetUrl) return;
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("open_browser_window", { url: targetUrl });
      setWindowOpen(true);
    } catch {
      // In dev mode without Tauri, open externally
      window.open(targetUrl, "_blank");
    }
  }, []);

  const handleNavigate = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    let navUrl = addressBar.trim();
    if (!navUrl) return;
    if (!navUrl.startsWith("http://") && !navUrl.startsWith("https://")) {
      navUrl = "https://" + navUrl;
    }
    setUrl(navUrl);
    openInTauri(navUrl);
  }, [addressBar, openInTauri]);

  const handleBack = useCallback(async () => {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      // Can't go back in a Tauri external webview easily, but we can try
    } catch {}
  }, []);

  return (
    <div className="flex flex-col h-full bg-bg overflow-hidden">
      {/* Nav bar */}
      <div className="flex items-center gap-1.5 px-2 py-1.5 border-b border-border-soft bg-sidebar shrink-0">
        <button onClick={handleBack} className="p-1 rounded text-dim hover:text-muted hover:bg-panel-hover">
          <ArrowLeft size={14} />
        </button>
        <button className="p-1 rounded text-dim hover:text-muted hover:bg-panel-hover">
          <ArrowRight size={14} />
        </button>
        <button onClick={() => url && openInTauri(url)} className="p-1 rounded text-dim hover:text-muted hover:bg-panel-hover">
          <RotateCw size={13} />
        </button>
        <form onSubmit={handleNavigate} className="flex-1">
          <div className="flex items-center gap-1.5 px-3 py-1 rounded-md bg-panel border border-border-soft focus-within:border-accent/40">
            <Globe size={11} className="text-dim shrink-0" />
            <input
              value={addressBar}
              onChange={(e) => setAddressBar(e.target.value)}
              placeholder="Enter URL..."
              className="flex-1 bg-transparent text-xs text-text placeholder:text-dim outline-none font-mono"
            />
          </div>
        </form>
        <button onClick={() => url && window.open(url, "_blank")} className="p-1 rounded text-dim hover:text-muted hover:bg-panel-hover" title="Open in system browser">
          <ExternalLink size={13} />
        </button>
      </div>

      {/* Status area */}
      <div className="flex-1 flex flex-col items-center justify-center gap-4 p-6 text-center">
        <div className="w-16 h-16 rounded-2xl bg-accent/10 border border-accent/20 flex items-center justify-center">
          <Monitor size={28} className="text-accent" />
        </div>

        <div>
          <p className="text-sm font-medium text-text mb-1">
            {windowOpen ? "Browser Window Open" : "Browser Automation"}
          </p>
          <p className="text-xs text-dim max-w-xs">
            {windowOpen
              ? "The browser is open in a separate KRYTH window. The agent is controlling it."
              : "The browser window will open when the agent navigates to a webpage."}
          </p>
        </div>

        {url && (
          <div className="w-full max-w-sm rounded-lg border border-border-soft bg-panel p-3 space-y-2">
            <div className="flex items-center gap-2">
              <Globe size={12} className="text-accent shrink-0" />
              <span className="text-xs font-mono text-text truncate">{url}</span>
            </div>
            {title && <p className="text-[11px] text-muted truncate">{title}</p>}
            {action && (
              <div className="flex items-center gap-2 px-2 py-1 rounded bg-accent/10 text-xs text-accent">
                <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
                {action}
              </div>
            )}
          </div>
        )}

        {!url && !windowOpen && (
          <form onSubmit={handleNavigate} className="w-full max-w-sm mt-2">
            <div className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-panel border border-border-soft focus-within:border-accent/40">
              <Globe size={12} className="text-dim" />
              <input
                value={addressBar}
                onChange={(e) => setAddressBar(e.target.value)}
                placeholder="Enter URL to open..."
                className="flex-1 bg-transparent text-sm text-text placeholder:text-dim outline-none"
              />
            </div>
          </form>
        )}
      </div>
    </div>
  );
});
