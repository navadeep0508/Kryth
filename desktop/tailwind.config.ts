import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        /* Light theme surface stack */
        bg:       "#F8FAFC",
        surface:  "#FFFFFF",
        surface2: "#F1F5F9",
        surface3: "#E8EDF3",
        /* Borders */
        border:   "#E5E7EB",
        "border-strong": "#D1D5DB",
        /* Text */
        text:     "#111827",
        "text-2": "#374151",
        muted:    "#6B7280",
        subtle:   "#9CA3AF",
        /* Brand */
        accent:   "#E8FF3A",
        "accent-fg": "#0A0A00",
        /* Semantic */
        success:  "#10B981",
        warning:  "#F59E0B",
        error:    "#EF4444",
        info:     "#3B82F6",
        /* Sidebar/topbar */
        chrome:   "#FFFFFF",
      },
      borderRadius: {
        xs:    "4px",
        sm:    "6px",
        md:    "8px",
        lg:    "12px",
        xl:    "16px",
        "2xl": "20px",
        "3xl": "24px",
      },
      fontFamily: {
        sans: ["Inter", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "sans-serif"],
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
        "slide-up":   { "0%": { opacity: "0", transform: "translateY(6px)" }, "100%": { opacity: "1", transform: "translateY(0)" } },
        "slide-down": { "0%": { opacity: "0", transform: "translateY(-6px)" }, "100%": { opacity: "1", transform: "translateY(0)" } },
        "slide-right":{ "0%": { opacity: "0", transform: "translateX(-6px)" }, "100%": { opacity: "1", transform: "translateX(0)" } },
        "pulse-dot":  { "0%, 100%": { opacity: "1" }, "50%": { opacity: "0.3" } },
        "pulse-soft": { "0%, 100%": { opacity: "1" }, "50%": { opacity: "0.45" } },
        "spin-slow":  { "0%": { transform: "rotate(0deg)" }, "100%": { transform: "rotate(360deg)" } },
        "shimmer":    { "0%": { backgroundPosition: "-200% 0" }, "100%": { backgroundPosition: "200% 0" } },
      },
      animation: {
        "fade-in":     "fade-in 140ms ease-out",
        "slide-up":    "slide-up 160ms cubic-bezier(0.16, 1, 0.3, 1)",
        "slide-down":  "slide-down 160ms cubic-bezier(0.16, 1, 0.3, 1)",
        "slide-right": "slide-right 160ms cubic-bezier(0.16, 1, 0.3, 1)",
        "pulse-dot":   "pulse-dot 1.4s ease-in-out infinite",
        "pulse-soft":  "pulse-soft 2s ease-in-out infinite",
        "spin-slow":   "spin-slow 1.2s linear infinite",
        "shimmer":     "shimmer 1.5s infinite linear",
      },
      transitionDuration: {
        "120": "120ms",
      },
      boxShadow: {
        "xs":     "0 1px 2px 0 rgba(0,0,0,0.04)",
        "sm":     "0 1px 3px 0 rgba(0,0,0,0.07), 0 1px 2px -1px rgba(0,0,0,0.06)",
        "md":     "0 4px 6px -1px rgba(0,0,0,0.07), 0 2px 4px -2px rgba(0,0,0,0.05)",
        "lg":     "0 10px 15px -3px rgba(0,0,0,0.08), 0 4px 6px -4px rgba(0,0,0,0.06)",
        "xl":     "0 20px 25px -5px rgba(0,0,0,0.09), 0 8px 10px -6px rgba(0,0,0,0.06)",
        "glow":   "0 0 0 3px rgba(232,255,58,0.25)",
        "accent": "0 0 0 2px #E8FF3A",
        "panel":  "0 0 0 1px rgba(0,0,0,0.06), 0 4px 16px rgba(0,0,0,0.08)",
        "modal":  "0 0 0 1px rgba(0,0,0,0.08), 0 16px 40px rgba(0,0,0,0.14)",
        "float":  "0 0 0 1px rgba(0,0,0,0.06), 0 8px 24px rgba(0,0,0,0.10)",
      },
      transitionTimingFunction: {
        "spring": "cubic-bezier(0.16, 1, 0.3, 1)",
        "snappy": "cubic-bezier(0.4, 0, 0.2, 1)",
      },
    },
  },
  plugins: [],
};

export default config;
