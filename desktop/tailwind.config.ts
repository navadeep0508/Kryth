import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        /* Dark grayscale surface stack (Codex-style) */
        bg:       "#0A0A0A",
        surface:  "#111111",
        surface2: "#171717",
        surface3: "#1F1F1F",
        hover:    "#1F1F1F",
        active:   "#27272A",
        /* Borders — very subtle white alpha */
        border:        "#FFFFFF0D",   /* 5%  */
        "border-soft": "#FFFFFF08",   /* 3%  */
        "border-mid":  "#FFFFFF14",   /* 8%  */
        "border-strong": "#FFFFFF1F", /* 12% */
        /* Text hierarchy */
        text:    "#FAFAFA",
        "text-2":"#A1A1AA",
        muted:   "#71717A",
        subtle:  "#52525B",
        /* Brand — lime used <5% of UI */
        accent:     "#DFFF00",
        "accent-fg":"#000000",
        /* Semantic */
        success: "#22C55E",
        warning: "#F59E0B",
        error:   "#EF4444",
        info:    "#3B82F6",
        /* Top bar chrome */
        chrome:  "#0D0D0D",
      },
      borderRadius: {
        xs:    "4px",
        sm:    "6px",
        md:    "8px",
        lg:    "12px",
        xl:    "16px",
        "2xl": "20px",
      },
      fontFamily: {
        sans: ["Inter", "Geist", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "sans-serif"],
        mono: ["JetBrains Mono", "Cascadia Code", "Fira Code", "Menlo", "monospace"],
      },
      fontSize: {
        "2xs": ["10px", { lineHeight: "14px" }],
        xs:    ["11px", { lineHeight: "16px" }],
        sm:    ["12px", { lineHeight: "18px" }],
        base:  ["13px", { lineHeight: "20px" }],
        md:    ["14px", { lineHeight: "22px" }],
        lg:    ["15px", { lineHeight: "24px" }],
        xl:    ["17px", { lineHeight: "26px" }],
      },
      keyframes: {
        "fade-in":    { "0%": { opacity: "0" }, "100%": { opacity: "1" } },
        "slide-up":   { "0%": { opacity: "0", transform: "translateY(4px)" }, "100%": { opacity: "1", transform: "translateY(0)" } },
        "slide-down": { "0%": { opacity: "0", transform: "translateY(-4px)" }, "100%": { opacity: "1", transform: "translateY(0)" } },
        "slide-right":{ "0%": { opacity: "0", transform: "translateX(-4px)" }, "100%": { opacity: "1", transform: "translateX(0)" } },
        "pulse-dot":  { "0%, 100%": { opacity: "1" }, "50%": { opacity: "0.3" } },
        "pulse-soft": { "0%, 100%": { opacity: "1" }, "50%": { opacity: "0.4" } },
        "spin-slow":  { "0%": { transform: "rotate(0deg)" }, "100%": { transform: "rotate(360deg)" } },
        "shimmer":    { "0%": { backgroundPosition: "-200% 0" }, "100%": { backgroundPosition: "200% 0" } },
        "cursor-blink": { "0%, 100%": { opacity: "1" }, "50%": { opacity: "0" } },
      },
      animation: {
        "fade-in":      "fade-in 100ms ease-out",
        "slide-up":     "slide-up 120ms cubic-bezier(0.16, 1, 0.3, 1)",
        "slide-down":   "slide-down 120ms cubic-bezier(0.16, 1, 0.3, 1)",
        "slide-right":  "slide-right 120ms cubic-bezier(0.16, 1, 0.3, 1)",
        "pulse-dot":    "pulse-dot 1.6s ease-in-out infinite",
        "pulse-soft":   "pulse-soft 2s ease-in-out infinite",
        "spin-slow":    "spin-slow 1.2s linear infinite",
        "shimmer":      "shimmer 1.5s infinite linear",
        "cursor-blink": "cursor-blink 1s step-end infinite",
      },
      transitionDuration: {
        "100": "100ms",
        "120": "120ms",
        "150": "150ms",
      },
      boxShadow: {
        "xs":    "0 1px 2px rgba(0,0,0,0.4)",
        "sm":    "0 1px 4px rgba(0,0,0,0.5)",
        "md":    "0 4px 8px rgba(0,0,0,0.6)",
        "lg":    "0 8px 20px rgba(0,0,0,0.7)",
        "panel": "0 0 0 1px rgba(255,255,255,0.06), 0 4px 16px rgba(0,0,0,0.5)",
        "modal": "0 0 0 1px rgba(255,255,255,0.08), 0 16px 48px rgba(0,0,0,0.8)",
        "float": "0 0 0 1px rgba(255,255,255,0.06), 0 8px 24px rgba(0,0,0,0.6)",
        "input": "0 0 0 1px rgba(255,255,255,0.08)",
        "input-focus": "0 0 0 1px rgba(255,255,255,0.16)",
      },
    },
  },
  plugins: [],
};

export default config;
