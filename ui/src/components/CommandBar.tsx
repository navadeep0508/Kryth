import React, { useRef, useState, useCallback, useEffect } from "react";
import { useStore } from "../hooks/useStore";

const HISTORY_MAX = 50;

export function CommandBar() {
  const store = useStore();
  const inputRef = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState("");
  const [history, setHistory] = useState<string[]>([]);
  const [historyIdx, setHistoryIdx] = useState(-1);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [suggestionIdx, setSuggestionIdx] = useState(-1);

  const SLASH_COMMANDS = [
    "/dag", "/swarm", "/clear", "/new", "/history",
    "/files", "/help", "/status", "/diag", "/model",
  ];

  useEffect(() => {
    const handleGlobalKey = (e: KeyboardEvent) => {
      if (
        document.activeElement !== inputRef.current &&
        e.key.length === 1 &&
        !e.ctrlKey &&
        !e.metaKey
      ) {
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", handleGlobalKey);
    return () => window.removeEventListener("keydown", handleGlobalKey);
  }, []);

  const updateSuggestions = useCallback(
    (val: string) => {
      if (val.startsWith("/")) {
        const q = val.toLowerCase();
        setSuggestions(SLASH_COMMANDS.filter((c) => c.startsWith(q)));
      } else if (val.includes("@")) {
        const after = val.split("@").pop()?.toLowerCase() || "";
        setSuggestions(
          ["package.json", "tsconfig.json", "App.tsx", "main.tsx"].filter((f) =>
            f.startsWith(after),
          ),
        );
      } else {
        setSuggestions([]);
      }
      setSuggestionIdx(-1);
    },
    [],
  );

  const execute = useCallback(
    (cmd: string) => {
      if (!cmd.trim()) return;
      setHistory((prev) => [cmd, ...prev].slice(0, HISTORY_MAX));
      setHistoryIdx(-1);
      setValue("");
      setSuggestions([]);

      if (cmd.startsWith("/")) {
        const parts = cmd.split(" ");
        switch (parts[0]) {
          case "/clear":
            store.clearBuffer();
            break;
          case "/help":
            store.writeln("Commands: /dag, /swarm, /clear, /new, /history, /files, /help, /status, /diag, /model");
            break;
          default:
            store.writeln(`Unknown command: ${cmd}`);
        }
      } else {
        store.writeln(`> ${cmd}`);
      }
    },
    [store],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault();
        if (suggestions.length > 0 && suggestionIdx >= 0) {
          const suffix = value.startsWith("/")
            ? suggestions[suggestionIdx].slice(value.length)
            : suggestions[suggestionIdx];
          setValue((prev) => prev + suffix);
          setSuggestions([]);
          setSuggestionIdx(-1);
          return;
        }
        execute(value);
        return;
      }

      if (e.key === "ArrowUp") {
        e.preventDefault();
        if (suggestions.length > 0) {
          setSuggestionIdx((prev) =>
            prev <= 0 ? suggestions.length - 1 : prev - 1,
          );
          return;
        }
        if (history.length > 0) {
          const next = Math.min(historyIdx + 1, history.length - 1);
          setHistoryIdx(next);
          setValue(history[next]);
        }
        return;
      }

      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (suggestions.length > 0) {
          setSuggestionIdx((prev) =>
            prev >= suggestions.length - 1 ? 0 : prev + 1,
          );
          return;
        }
        if (historyIdx > 0) {
          const next = historyIdx - 1;
          setHistoryIdx(next);
          setValue(history[next]);
        } else {
          setHistoryIdx(-1);
          setValue("");
        }
        return;
      }

      if (e.key === "Tab" && suggestions.length > 0) {
        e.preventDefault();
        const next = e.shiftKey
          ? suggestionIdx <= 0
            ? suggestions.length - 1
            : suggestionIdx - 1
          : suggestionIdx >= suggestions.length - 1
            ? 0
            : suggestionIdx + 1;
        setSuggestionIdx(next);
        return;
      }
    },
    [value, suggestions, suggestionIdx, history, historyIdx, execute],
  );

  return (
    <div className="kryth-command-bar" style={{ position: "relative" }}>
      {suggestions.length > 0 && (
        <div
          style={{
            position: "absolute",
            bottom: "100%",
            left: 0,
            right: 0,
            background: "var(--bg-panel)",
            border: "1px solid var(--border)",
            borderBottom: "none",
            maxHeight: 200,
            overflow: "auto",
          }}
        >
          {suggestions.map((s, i) => (
            <div
              key={s}
              style={{
                padding: "4px 16px",
                cursor: "pointer",
                background:
                  i === suggestionIdx ? "var(--bg-active)" : "transparent",
                color:
                  i === suggestionIdx
                    ? "var(--accent-active)"
                    : "var(--text-secondary)",
                fontFamily: "var(--font-mono)",
                fontSize: "var(--font-size)",
              }}
              onMouseDown={() => {
                setValue(s);
                setSuggestions([]);
                inputRef.current?.focus();
              }}
            >
              {s}
            </div>
          ))}
        </div>
      )}
      <input
        ref={inputRef}
        className="kryth-command-input"
        type="text"
        placeholder="Type a command or ask a question..."
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          updateSuggestions(e.target.value);
        }}
        onKeyDown={handleKeyDown}
        spellCheck={false}
        autoComplete="off"
      />
    </div>
  );
}
