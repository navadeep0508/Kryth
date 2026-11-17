# kuba-browser CLI — one-time install prerequisite

This is a **one-time prerequisite**, not part of the regular AI workflow. Do it once; after `kuba-browser` prints page info, never repeat install/connection steps during normal browser work.

## Install the command

```bash
uv tool install --python 3.12 --upgrade --force kuba-browser
command -v kuba-browser   # should print a path
```

`--python 3.12` prevents uv from selecting old releases that support older Python versions. `--upgrade --force` replaces any previous `kuba-browser` tool install with the latest stable release. It does not uninstall unrelated commands such as `browser-use-Browser` or `browser-use-Terminal`.

For kuba-browser development, clone the repo into a durable path and run `uv tool install -e .` from the checkout.

## Register the skill

Install/register a skill named `kuba-browser` using this command for the body:

```bash
kuba-browser skill
```

For Codex:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills/kuba-browser"
kuba-browser skill > "${CODEX_HOME:-$HOME/.codex}/skills/kuba-browser/SKILL.md"
```

If an old user-installed `browser` or `browser-use` skill is being picked instead, remove that stale skill directory manually. Never edit bundled/vendor plugin caches.

## Connect to a browser

`kuba-browser` attaches to a Chrome you already have running, or to a Browser Use cloud browser. Quick check:

```bash
kuba-browser <<'PY'
print(page_info())
PY
```

If that prints page info, you're done. If not, run `kuba-browser --doctor` and follow the connection cases. The two connection methods:

- **Way 1 (real browser):** open Chrome normally, then open `chrome://inspect/#remote-debugging` and tick "Allow remote debugging for this browser instance". On Chrome 144+, click Allow on the first-attach popup. Inherits your logins/extensions — best when the agent acts in your everyday browser.
- **Way 2 (isolated profile, no popups):** launch Chrome with `--remote-debugging-port=9222 --user-data-dir=<non-default path>`, then set `BU_CDP_URL=http://127.0.0.1:9222`. Best for unattended automation.

If the quick path fails after `--doctor`, inspect `src/kuba_browser/admin.py`, `src/kuba_browser/daemon.py`, and `src/kuba_browser/_ipc.py`.

## Keeping current

`kuba-browser` prints an update banner when a newer PyPI release exists; run `kuba-browser --update -y` when you decide to upgrade. `kuba-browser --doctor` also checks the latest version. Telemetry is anonymous and opt-out with `kuba-browser telemetry disable`.

State lives under `${XDG_CONFIG_HOME:-~/.config}/kuba-browser` by default: auth, agent workspace, runtime sockets, logs, screenshots, and temp files. Override with `KUBA_HOME` or `kuba_browser_HOME`.
