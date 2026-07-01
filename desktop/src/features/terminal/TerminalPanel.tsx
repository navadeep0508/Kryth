import { memo, useEffect, useRef, useCallback } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";

const WS_URL = "ws://127.0.0.1:7765/ws/shell";
const MAX_RETRIES = 5;
const BACKOFF_DELAYS = [2000, 4000, 8000, 16000, 30000];

const TERMINAL_THEME = {
  background:      "#FFFFFF",
  foreground:      "#1F2328",
  cursor:          "#0969DA",
  cursorAccent:    "#FFFFFF",
  selectionBackground: "rgba(9,105,218,0.2)",
  black:           "#1F2328",
  brightBlack:     "#656D76",
  red:             "#CF222E",
  brightRed:       "#A40E26",
  green:           "#1A7F37",
  brightGreen:     "#116329",
  yellow:          "#9A6700",
  brightYellow:    "#7D4E00",
  blue:            "#0969DA",
  brightBlue:      "#0550AE",
  magenta:         "#8250DF",
  brightMagenta:   "#6639BA",
  cyan:            "#1B7C83",
  brightCyan:      "#136066",
  white:           "#656D76",
  brightWhite:     "#1F2328",
};

export default memo(function TerminalPanel() {
  const containerRef  = useRef<HTMLDivElement>(null);
  const termRef       = useRef<Terminal | null>(null);
  const fitRef        = useRef<FitAddon | null>(null);
  const wsRef         = useRef<WebSocket | null>(null);
  const retryRef      = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const disposedRef   = useRef(false);

  const connectWs = useCallback(() => {
    const term = termRef.current;
    const fitAddon = fitRef.current;
    if (!term || disposedRef.current) return;

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
      retryRef.current = 0;
      if (fitAddon) fitAddon.fit();
    };

    ws.onmessage = (evt) => {
      if (evt.data instanceof ArrayBuffer) {
        term.write(new Uint8Array(evt.data));
      } else {
        term.write(evt.data);
      }
    };

    ws.onclose = () => {
      if (disposedRef.current) return;

      const attempt = retryRef.current;
      if (attempt < MAX_RETRIES) {
        const delay = BACKOFF_DELAYS[attempt] ?? 30000;
        term.write("\r\n\x1b[33m[Reconnecting...]\x1b[0m\r\n");
        retryRef.current = attempt + 1;
        retryTimerRef.current = setTimeout(() => {
          if (!disposedRef.current) connectWs();
        }, delay);
      } else {
        term.write("\r\n\x1b[31m[Connection failed. Click to retry]\x1b[0m\r\n");
      }
    };

    // Forward keystrokes
    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(data);
    });
  }, []);

  const handleContainerClick = useCallback(() => {
    // Only trigger manual reconnect if max retries exhausted
    if (retryRef.current >= MAX_RETRIES && termRef.current) {
      retryRef.current = 0;
      termRef.current.write("\r\n\x1b[33m[Reconnecting...]\x1b[0m\r\n");
      connectWs();
    }
  }, [connectWs]);

  useEffect(() => {
    if (!containerRef.current) return;
    disposedRef.current = false;

    const term = new Terminal({
      theme: TERMINAL_THEME,
      fontFamily: "'JetBrains Mono', 'Fira Code', Menlo, monospace",
      fontSize: 12,
      lineHeight: 1.4,
      cursorBlink: true,
      cursorStyle: "block",
      scrollback: 5000,
      allowTransparency: true,
    });

    const fitAddon   = new FitAddon();
    const linksAddon = new WebLinksAddon();
    term.loadAddon(fitAddon);
    term.loadAddon(linksAddon);
    term.open(containerRef.current);
    fitAddon.fit();

    termRef.current = term;
    fitRef.current  = fitAddon;

    // Initial connection
    connectWs();

    // Resize observer
    const ro = new ResizeObserver(() => {
      fitAddon.fit();
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        const dims = fitAddon.proposeDimensions();
        if (dims) {
          ws.send(JSON.stringify({ type: "resize", cols: dims.cols, rows: dims.rows }));
        }
      }
    });
    if (containerRef.current) ro.observe(containerRef.current);

    return () => {
      disposedRef.current = true;
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
      ro.disconnect();
      if (wsRef.current) wsRef.current.close();
      term.dispose();
    };
  }, [connectWs]);

  return (
    <div
      ref={containerRef}
      onClick={handleContainerClick}
      className="w-full h-full"
      style={{ padding: "8px", background: "#FFFFFF" }}
    />
  );
});
