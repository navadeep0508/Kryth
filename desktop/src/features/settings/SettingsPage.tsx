import React, { memo, useEffect, useState } from "react";
import { Save, RotateCcw } from "lucide-react";
import { bridge } from "@/lib/krythBridge";

const PERMISSION_PROFILES = ["readonly", "safe", "default", "yolo"] as const;
const EXEC_PROFILES = ["fast", "balanced", "max"] as const;

interface Config {
  KRYTH_MAIN_MODEL: string;
  KRYTH_BASE_URL: string;
  KRYTH_PERMISSIONS: string;
  KRYTH_EXEC_PROFILE: string;
  KRYTH_FAST_MODEL: string;
  [key: string]: string;
}

export default memo(function SettingsPage() {
  const [config, setConfig] = useState<Config>({
    KRYTH_MAIN_MODEL: "",
    KRYTH_BASE_URL: "",
    KRYTH_PERMISSIONS: "default",
    KRYTH_EXEC_PROFILE: "balanced",
    KRYTH_FAST_MODEL: "",
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    bridge.getConfig()
      .then((cfg) => setConfig((prev) => ({ ...prev, ...cfg })))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const save = async (key: string, value: string) => {
    setSaving(key);
    try {
      await bridge.patchConfig(key, value);
      setSaved(key);
      setTimeout(() => setSaved(null), 1500);
    } catch {
      // silently ignore
    } finally {
      setSaving(null);
    }
  };

  const set = (key: string, value: string) => {
    setConfig((prev) => ({ ...prev, [key]: value }));
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted text-sm">
        Loading settings…
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-8 max-w-2xl mx-auto">
      <h1 className="text-lg font-semibold text-text mb-6">Settings</h1>

      <Section title="Model">
        <Field label="Main Model" hint="e.g. claude-sonnet-4-6">
          <Input
            value={config.KRYTH_MAIN_MODEL}
            onChange={(v) => set("KRYTH_MAIN_MODEL", v)}
            onBlur={() => save("KRYTH_MAIN_MODEL", config.KRYTH_MAIN_MODEL)}
            saving={saving === "KRYTH_MAIN_MODEL"}
            saved={saved === "KRYTH_MAIN_MODEL"}
          />
        </Field>
        <Field label="Fast Model" hint="Used for quick tasks (optional)">
          <Input
            value={config.KRYTH_FAST_MODEL}
            onChange={(v) => set("KRYTH_FAST_MODEL", v)}
            onBlur={() => save("KRYTH_FAST_MODEL", config.KRYTH_FAST_MODEL)}
            saving={saving === "KRYTH_FAST_MODEL"}
            saved={saved === "KRYTH_FAST_MODEL"}
          />
        </Field>
        <Field label="Base URL" hint="Custom OpenAI-compatible endpoint">
          <Input
            value={config.KRYTH_BASE_URL}
            onChange={(v) => set("KRYTH_BASE_URL", v)}
            onBlur={() => save("KRYTH_BASE_URL", config.KRYTH_BASE_URL)}
            saving={saving === "KRYTH_BASE_URL"}
            saved={saved === "KRYTH_BASE_URL"}
          />
        </Field>
      </Section>

      <Section title="Permissions">
        <div className="grid grid-cols-4 gap-2">
          {PERMISSION_PROFILES.map((p) => (
            <button
              key={p}
              onClick={() => { set("KRYTH_PERMISSIONS", p); save("KRYTH_PERMISSIONS", p); }}
              className={[
                "py-2 rounded-lg text-xs font-medium border transition-colors duration-120 capitalize",
                config.KRYTH_PERMISSIONS === p
                  ? "border-accent bg-accent/10 text-accent"
                  : "border-border text-muted hover:text-text",
              ].join(" ")}
            >
              {p}
            </button>
          ))}
        </div>
        <p className="text-xs text-muted mt-2">
          {config.KRYTH_PERMISSIONS === "readonly" && "Read files only — no writes or commands."}
          {config.KRYTH_PERMISSIONS === "safe"     && "Requires approval for all file writes and commands."}
          {config.KRYTH_PERMISSIONS === "default"  && "Approves low-risk actions automatically."}
          {config.KRYTH_PERMISSIONS === "yolo"     && "Auto-approves everything. Use with caution."}
        </p>
      </Section>

      <Section title="Execution Profile">
        <div className="grid grid-cols-3 gap-2">
          {EXEC_PROFILES.map((p) => (
            <button
              key={p}
              onClick={() => { set("KRYTH_EXEC_PROFILE", p); save("KRYTH_EXEC_PROFILE", p); }}
              className={[
                "py-2 rounded-lg text-xs font-medium border transition-colors duration-120 capitalize",
                config.KRYTH_EXEC_PROFILE === p
                  ? "border-accent bg-accent/10 text-accent"
                  : "border-border text-muted hover:text-text",
              ].join(" ")}
            >
              {p}
            </button>
          ))}
        </div>
        <p className="text-xs text-muted mt-2">
          {config.KRYTH_EXEC_PROFILE === "fast"     && "Single-pass execution, minimal context."}
          {config.KRYTH_EXEC_PROFILE === "balanced" && "Standard parallel execution with planning."}
          {config.KRYTH_EXEC_PROFILE === "max"      && "Full orchestration, maximum quality."}
        </p>
      </Section>
    </div>
  );
});

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-8">
      <h2 className="text-xs font-semibold text-muted uppercase tracking-wide mb-3">{title}</h2>
      <div className="space-y-4">{children}</div>
    </section>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-sm text-text mb-1">{label}</label>
      {hint && <p className="text-xs text-muted mb-1.5">{hint}</p>}
      {children}
    </div>
  );
}

function Input({
  value, onChange, onBlur, saving, saved,
}: {
  value: string;
  onChange: (v: string) => void;
  onBlur: () => void;
  saving: boolean;
  saved: boolean;
}) {
  return (
    <div className="relative">
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
        className="w-full px-3 py-2 rounded-lg border border-border bg-surface2 text-sm text-text placeholder:text-muted outline-none focus:border-accent/50 transition-colors duration-120 pr-8"
      />
      {saving && (
        <div className="absolute right-2 top-1/2 -translate-y-1/2">
          <RotateCcw size={13} className="text-muted animate-spin" />
        </div>
      )}
      {saved && (
        <div className="absolute right-2 top-1/2 -translate-y-1/2">
          <Save size={13} className="text-success" />
        </div>
      )}
    </div>
  );
}
