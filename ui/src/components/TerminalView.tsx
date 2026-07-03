import React, { useEffect, useRef, useCallback } from "react";
import { useStore } from "../hooks/useStore";
import { DiffRenderer } from "../../runtime/diff_renderer";

interface TerminalViewProps {
  width: number;
  height: number;
}

const CHAR_W = 8.4;
const CHAR_H = 20;
const PADDING = 8;

export function TerminalView({ width, height }: TerminalViewProps) {
  const store = useStore();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const diffRef = useRef<DiffRenderer>(new DiffRenderer());
  const rafRef = useRef<number>(0);

  const cols = Math.max(20, Math.floor((width - PADDING * 2) / CHAR_W));
  const rows = Math.max(5, Math.floor((height - PADDING * 2) / CHAR_H));

  useEffect(() => {
    store.resizeBuffer(rows, cols);
  }, [rows, cols, store]);

  const render = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth * dpr;
    const h = canvas.clientHeight * dpr;
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    ctx.scale(dpr, dpr);

    const buf = store.buffer;
    const diff = diffRef.current.diff(buf);

    if (diff.full) {
      ctx.fillStyle = "#090909";
      ctx.fillRect(0, 0, canvas.clientWidth, canvas.clientHeight);
    }

    ctx.font = '13px "JetBrains Mono", "Geist Mono", monospace';
    ctx.textBaseline = "top";

    for (const region of diff.regions) {
      const r = region.row;
      const y = PADDING + r * CHAR_H;

      for (let c = region.startCol; c <= region.endCol && c < buf.cols; c++) {
        const cell = buf.cells[r]?.[c];
        if (!cell) continue;

        const x = PADDING + c * CHAR_W;
        const char = cell.char;

        const bg = cell.style.bg;
        if (bg !== 0) {
          ctx.fillStyle = _ansiPalette[bg] ?? "#111111";
          ctx.fillRect(x, y, CHAR_W, CHAR_H);
        }

        if (cell.style.inverse) {
          ctx.fillStyle = "#f5f5f5";
          ctx.fillRect(x, y, CHAR_W, CHAR_H);
          ctx.fillStyle = "#090909";
        } else {
          ctx.fillStyle = _ansiPalette[cell.style.fg] ?? "#f5f5f5";
        }

        if (cell.style.bold) {
          ctx.font = 'bold 13px "JetBrains Mono", "Geist Mono", monospace';
        } else {
          ctx.font = '13px "JetBrains Mono", "Geist Mono", monospace';
        }

        if (char !== " " && char !== "") {
          ctx.fillText(char, x, y);
        }

        if (cell.style.underline) {
          ctx.beginPath();
          ctx.moveTo(x, y + CHAR_H - 2);
          ctx.lineTo(x + CHAR_W, y + CHAR_H - 2);
          ctx.stroke();
        }
      }
    }

    // Cursor
    const cursor = store.cursor;
    if (cursor.visible && cursor.row < buf.rows && cursor.col < buf.cols) {
      const cx = PADDING + cursor.col * CHAR_W;
      const cy = PADDING + cursor.row * CHAR_H;
      ctx.fillStyle = "#60a5fa";
      ctx.globalAlpha = 0.6;
      ctx.fillRect(cx, cy, 2, CHAR_H);
      ctx.globalAlpha = 1;
    }

    buf.clearDirty();
  }, [store]);

  useEffect(() => {
    const loop = () => {
      render();
      rafRef.current = requestAnimationFrame(loop);
    };
    rafRef.current = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(rafRef.current);
  }, [render]);

  const canvasW = Math.max(100, width);
  const canvasH = Math.max(100, height);

  return (
    <div className="kryth-terminal-view">
      <canvas
        ref={canvasRef}
        className="kryth-terminal-canvas"
        style={{ width: canvasW, height: canvasH }}
      />
    </div>
  );
}

// ANSI color map
const _ansiPalette: Record<number, string> = {
  0: "#090909", 1: "#f87171", 2: "#4ade80", 3: "#facc15",
  4: "#60a5fa", 5: "#818cf8", 6: "#22d3ee", 7: "#f5f5f5",
  8: "#5c5c5c", 9: "#f87171", 10: "#4ade80", 11: "#facc15",
  12: "#60a5fa", 13: "#818cf8", 14: "#22d3ee", 15: "#ffffff",
};
