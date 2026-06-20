import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0B0F19",
        surface: "#111827",
        surface2: "#1A2332",
        border: "#1F2937",
        text: "#F9FAFB",
        muted: "#9CA3AF",
        accent: "#E8FF3A",
        success: "#10B981",
        warning: "#F59E0B",
        error: "#EF4444",
      },
      borderRadius: {
        panel: "16px",
        card: "20px",
      },
      fontFamily: {
        sans: [
          "Inter",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "Cascadia Code",
          "Fira Code",
          "Menlo",
          "monospace",
        ],
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        "slide-up": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "slide-down": {
          "0%": { opacity: "0", transform: "translateY(-8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "pulse-soft": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.5" },
        },
      },
      animation: {
        "fade-in": "fade-in 120ms ease-out",
        "slide-up": "slide-up 150ms ease-out",
        "slide-down": "slide-down 150ms ease-out",
        "pulse-soft": "pulse-soft 1.5s ease-in-out infinite",
      },
      boxShadow: {
        panel: "0 4px 24px 0 rgba(0,0,0,0.4)",
        card: "0 2px 12px 0 rgba(0,0,0,0.3)",
        glow: "0 0 20px 0 rgba(232,255,58,0.15)",
      },
    },
  },
  plugins: [],
};

export default config;
