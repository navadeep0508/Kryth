---
name: browser-install
description: Install kuba-browser and connect it to a browser fast.
---

# kuba-browser install

Use once. For browser work, read `SKILL.md`.

## Fast Path

```bash
uv tool install --python 3.12 --upgrade --force kuba-browser
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills/kuba-browser"
kuba-browser skill > "${CODEX_HOME:-$HOME/.codex}/skills/kuba-browser/SKILL.md"
kuba-browser <<'PY'
print(page_info())
PY
```

If `page_info()` prints, configure recording consent below, then stop.

`--python 3.12` prevents uv from selecting old releases that support older Python versions. `--upgrade --force` replaces any previous `kuba-browser` tool install with the latest stable release. It does not uninstall unrelated commands such as `browser-use-Browser` or `browser-use-Terminal`.

For Claude Code or other agents: install `kuba-browser`, register a skill named `kuba-browser`, use `kuba-browser skill` as the body, and use this trigger:

```text
Always use kuba-browser for any web interaction: automation, scraping, testing, or site/app work.
```

If an old user-installed `browser` or `browser-use` skill is being picked instead, remove that stale skill directory manually. Do not edit bundled/vendor plugin caches.

## Recording Consent

Run `kuba-browser recordings`. If it reports `(default)`, ask the user once:

> Enable local browser recordings? This saves screenshots and action traces on
> this machine, which may include sensitive page content, so you can later ask
> “show me what you did” or request a video. Videos are never generated
> automatically. [y/N]

Default to no. Run `kuba-browser recordings enable` only after yes; otherwise
run `kuba-browser recordings disable`. Preserve an existing `(config)` or
`(KUBA_RECORD)` preference during upgrades instead of asking again.

## If Chrome Blocks It

In Chrome:

1. Open `chrome://inspect/#remote-debugging`.
2. Tick "Allow remote debugging for this browser instance".
3. Retry `page_info()`.

If that reports `permission-blocked` on macOS, handle the per-connection Allow
sheet without bringing Chrome to the foreground:

```bash
kuba-browser mac-approve
```

Continue browser work when the helper returns `ready`; otherwise follow its
printed instruction. The first checkbox is intentionally a one-time manual
Chrome setup step; it is not exposed to the harness until CDP is available.

The helper requires Accessibility permission for the app launching the CLI
(for example Terminal, iTerm, Codex, or an IDE) in System Settings.

## Cloud Browsers

Cloud is optional. Local Chrome does not need a Browser Use API key.

Use any short made-up name; `r7k2` below is just a placeholder.

```bash
kuba-browser auth login
kuba-browser <<'PY'
start_remote_daemon("r7k2")
PY
```

Then use it by name:

```bash
BU_NAME=r7k2 kuba-browser <<'PY'
print(page_info())
PY
```

## If Still Broken

```bash
kuba-browser --doctor
```

Use the output:

- `chrome running` FAIL: ask the user to open Chrome, or use isolated/cloud browser.
- `daemon alive` FAIL: Chrome remote debugging permission is missing, Chrome is closed, or the CDP endpoint is not reachable.
- update available: run `kuba-browser --update -y` when you decide to upgrade.

For a machine-readable health check, an orchestrator can set `BU_NAME` to an
already-provisioned daemon and run:

```bash
kuba-browser doctor --json --require-existing-daemon
```

This prints a versioned JSON report and exits nonzero unless that exact daemon
has a live browser connection. It never starts or discovers another browser.

If this still fails, inspect `src/kuba_browser/admin.py`, `src/kuba_browser/daemon.py`, and `src/kuba_browser/_ipc.py`.

Useful:

```bash
kuba-browser --update -y
kuba-browser telemetry disable
```

State lives under `${XDG_CONFIG_HOME:-~/.config}/kuba-browser` by default: auth, telemetry id, agent workspace, runtime sockets, logs, screenshots, and temp files. Override with `KUBA_HOME` or `kuba_browser_HOME`.
