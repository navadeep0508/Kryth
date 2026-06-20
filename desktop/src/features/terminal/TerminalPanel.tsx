import { memo, useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";

const WS_URL = "ws://127.0.0.1:7765/ws/shell";

const TERMINAL_THEME = {
  background:      "#0B0F19",
  foreground:      "#F9FAFB",
  cursor:          "#E8FF3A",
  cursorAccent:    "#0B0F19",
  selectionBackground: "rgba(232,255,58,0.2)",
  black:           "#1F2937",
  brightBlack:     "#374151",
  red:             "#EF4444",
  brightRed:       "#F87171",
  green:           "#10B981",
  brightGreen:     "#34D399",
  yellow:          "#F59E0B",
  brightYellow:    "#FCD34D",
  blue:            "#3B82F6",
  brightBlue:      "#60A5FA",
  magenta:         "#8B5CF6",
  brightMagenta:   "#A78BFA",
  cyan:            "#06B6D4",
  brightCyan:      "#22D3EE",
  white:           "#D1D5DB",
  brightWhite:     "#F9FAFB",
};

export default memo(function TerminalPanel() {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef      = useRef<Terminal | null>(null);
  const fitRef       = useRef<FitAddon | null>(null);
  const wsRef        = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

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

    // Connect WebSocket
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;
    ws.binaryType = "arraybuffer";

    ws.onopen = () => fitAddon.fit();

    ws.onmessage = (evt) => {
      if (evt.data instanceof ArrayBuffer) {
        term.write(new Uint8Array(evt.data));
      } else {
        term.write(evt.data);
      }
    };

    ws.onclose = () => term.write("\r\n\x1b[31m[Terminal disconnected]\x1b[0m\r\n");

    // Forward keystrokes
    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(data);
    });

    // Resize observer
    const ro = new ResizeObserver(() => {
      fitAddon.fit();
      if (ws.readyState === WebSocket.OPEN) {
        const dims = fitAddon.proposeDimensions();
        if (dims) {
          ws.send(JSON.stringify({ type: "resize", cols: dims.cols, rows: dims.rows }));
        }
      }
    });
    if (containerRef.current) ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      ws.close();
      term.dispose();
    };
  }, []);

  return (
    <div
      ref={containerRef}
      className="w-full h-full bg-bg"
      style={{ padding: "8px" }}
    />
  );
});
