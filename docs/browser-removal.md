# Browser Integration Removal

KRYTH is now a **browser-free** AI coding agent. All browser/extension integration has been intentionally removed from the project.

## Decision

This was an intentional product decision, not an accidental deletion. The KRYTH Browser extension (formerly KUBA browser) and all related integration code have been removed. KRYTH no longer depends on or interacts with any browser extension.

## What Was Removed

### Directories deleted
- `browser/` — The Chromium MV3 extension source (Solid + Vite), including `vendor/`, `public/`, `background/`, `status/`, etc.
- `kuba-browser-source/` — Previous browser source directory
- `vendor/` — Extension build source (icons, manifest, onboarding, relay-form, etc.)

### Files deleted
- `tools/browser_observation.py` — Browser page observation scripts
- `vendor/LICENSE` — Extension license file
- `docs/kuba-browser.md` — KUBA browser setup guide (already absent)

### Code removed from Python source
- `main.py` — Removed `browser` CLI command group registration
- `agent/session.py` — Removed `BrowserPluginManager` import, instantiation, and discovery block
- `agent/agent.py` — Removed browser tool phase mapping and browser lifecycle step tracking
- `ui/components/tool_call.py` — Removed `_browser()` renderer and `browser`/`browser_connect`/`browser_disconnect`/`browser_status` renderers
- `ui/repl/commands/dispatch.py` — Removed `browser_command` method and `browser` handler/hint
- `config/config.py` — Removed `KRYTHBrowserConfig` class and `kryth_browser` config field
- `pyproject.toml` — Removed `kryth_browser` package-dir, package-data, and include entry
- `prompts/tool_guidelines.py` — Removed "Browser workflow (KUBA)" section
- `context/project_context.py` — Removed `browser_infrastructure_root` property
- `ui/tui.py` — Removed "browser connected" reference from completion summary docstring

### Dependencies removed from `pyproject.toml`
- `Pillow>=10.0,<12` — Not imported anywhere
- `cdp-use==1.4.5` — Not imported anywhere (was Chrome DevTools Protocol for browser automation)

### CI workflow updated
- `.github/workflows/python-publish.yml` — Removed `browser/src` from compileall, removed `kuba_browser` import assertions, updated release notes

### README updated
- Removed "kryth browser doctor" / "kryth browser setup" instructions
- Removed "KUBA browser setup" from documentation table

## What Remains

The following browser-related references remain but are **not** browser integration:

- `config/oauth.py` — Uses `webbrowser.open()` only for OAuth login flow (opening a browser tab to authorize with OpenRouter)
- `main.py` — References "Open your browser" in login help text (OAuth login)
- `docs/cli.md`, `docs/technologies.md` — Document OAuth browser login
- `tests/*` — Some test fixtures reference "browser" in test data strings (e.g., "connect browser", "browser_search")
- `utils/semantic_verification.py` — Has `browser_search`, `browser_navigation`, `browser_video` verification kinds (part of general verification framework; no browser tool produces these kinds anymore)

## Impact

- KRYTH CLI entry point (`kryth = "main:main"`) unchanged
- All core tools (read, write, edit, bash, grep, glob, search, fetch, plan, memory, terminal, subagents, MCP) unchanged
- Package builds and installs correctly (`kryth-0.0.44-py3-none-any.whl`)
- Test suite passes (320 passed, 2 pre-existing environment-specific failures unrelated to browser removal)
