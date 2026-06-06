# KRYTH Browser & Agent Implementation - Comprehensive Search Results

## Overview
The workspace contains **two parallel browser automation systems**:
1. **Lightweight Playwright wrapper** - thread-isolated for UI compatibility
2. **Full browser-use agent** - comprehensive web automation framework

---

## 1. MAIN AGENT DIRECTORY: kryth/src/agent/

### Core Files (43 root .py files)
Framework: agent_loop.py, llm.py, skills.py, skill_library.py, model_router.py
State: context.py, context_manager.py, session.py, persistence.py, settings.py
Tools: _task_graph.py, _subagent.py, _shell.py, _file_ops.py, _git.py, _search.py, _verify.py, _critique.py
Planning: planner.py, _plan.py, _project_runner.py, _checkpoint.py, _debug.py, _results.py
Support: repo_index.py, retriever.py, vision.py, prompts.py, validators.py, permissions.py, hooks.py

---

## 2. LIGHTWEIGHT BROWSER SYSTEM: kryth/src/agent/browser/

Structure:
  browser_manager.py         - Main thread-isolated wrapper
  _human.py                  - Human-like behavior detection
  page_controller.py         - Page interaction control
  selectors.py              - DOM selector logic
  profiles.py               - Browser profiles
  downloads.py              - Download handling
  uploads.py                - File upload handling
  watchdogs/
    local_browser_watchdog.py

Key Feature: Thread Isolation for Playwright
- Playwright runs in dedicated daemon thread
- Thread-safe queue for communication
- Own event loop to avoid asyncio conflicts with prompt_toolkit

---

## 3. FULL BROWSER-USE AGENT: kryth/src/agent/browser-use/

### Files & Structure (165 Python files, ~6,924 lines)

Root Level:
  kryth_browser_bridge.py     - Integration bridge (Kryth ↔ browser_agent)
  run_nvidia_agent.py         - NVIDIA provider example
  test_browser_use_install.py - Tests

browser_agent/ Core:
  cli.py                  - Command-line interface
  config.py               - Configuration
  exceptions.py           - Exception definitions
  init_cmd.py             - Initialization
  logging_config.py       - Logging setup
  observability.py        - Observability/telemetry
  utils.py                - Utilities

Submodules:
  agent/                  - Agent core logic + system_prompts + message_manager
  actor/                  - Actor pattern implementation
  browser/                - Browser control + cloud + watchdogs
  controller/             - Page controller
  dom/                    - DOM handling + serializer
  filesystem/             - File system operations
  llm/                    - Multi-provider LLM support (15+ providers)
  mcp/                    - Model Context Protocol
  sandbox/                - Sandboxed execution
  screenshots/            - Screenshot handling
  skill_cli/              - Skill CLI integration
  skills/                 - Built-in skills
  sync/                   - Synchronization
  telemetry/              - Telemetry tracking
  tokens/                 - Token management
  tools/                  - Browser tools

LLM Provider Support (browser-use/llm/):
  anthropic/              - Anthropic Claude
  aws/                    - AWS Bedrock
  azure/                  - Azure OpenAI
  cerebras/               - Cerebras
  deepseek/               - DeepSeek
  google/                 - Google Gemini
  groq/                   - Groq
  litellm/                - LiteLLM (multi-provider)
  mistral/                - Mistral
  nvidia/                 - NVIDIA
  oci_raw/                - Oracle Cloud
  ollama/                 - Ollama (local)
  openai/                 - OpenAI
  openrouter/             - OpenRouter
  vercel/                 - Vercel

---

## 4. INTEGRATION POINTS

Main Agent Imports:
  from agent.tools._browser import check_browser_errors  (in __init__.py)
  from agent.browser.selectors import SelectorResult      (in vision.py)

Browser Tools (kryth/src/agent/tools/):
  _browser.py             - Console error checking via Playwright
  _opencli.py             - Browser references
  providers/playwright_browser.py - Playwright provider

Bridge (kryth/src/agent/bridge/):
  server.py               - Bridge server
  providers/openai_browser.py - OpenAI browser integration

---

## 5. KEY STATISTICS

Main agent .py files:          43
Browser-use .py files:         165
Total agent-related .py files: 288
browser-use code lines:        ~6,924
LLM provider integrations:     15+
Thread-isolated modules:       1 (browser_manager)

---

## 6. CAPABILITIES

Playwright-Based (Lightweight):
  - URL navigation with wait states
  - Console error/warning capture
  - Network error tracking
  - Page interactions
  - DOM selector queries
  - File uploads/downloads
  - Browser profile management

browser-use Agent (Full-Featured):
  - Multi-LLM support (15+ providers)
  - Vision capabilities (image understanding)
  - DOM serialization & analysis
  - Natural language task execution
  - Cloud provider support
  - Sandbox execution
  - Skill system integration
  - Model Context Protocol (MCP)
  - System prompts: vision, flash, no-thinking modes
  - Browser watchdog/monitoring
  - Actor pattern for parallel tasks
  - Telemetry & observability

---

## 7. TECHNICAL PATTERNS

Thread Safety (browser_manager.py):
  Problem: Playwright sync_api + prompt_toolkit asyncio = conflict
  Solution: Dedicated daemon thread with own event loop + thread-safe queue

Multi-LLM Providers (browser-use/llm/):
  - Base chat class with common interface
  - Provider-specific implementations
  - Lazy imports to avoid heavy dependencies
  - Config object pattern

System Prompts (browser-use/agent/system_prompts/):
  - Standard vision-capable prompt
  - Flash mode (faster) variant
  - No-thinking mode for older models
  - Markdown format for editing

---

## 8. EXECUTION FLOW

1. User Request → agent_loop.py
2. Model Decision → ask_llm_stream()
3. Tool Dispatch → TOOLS[tool_name](args)
4. Browser Options:
   - Simple: _browser.py:check_browser_errors() (Playwright)
   - Complex: KrythBrowserBridge → browser_agent
5. Result Capture → Loop continues until done

---

## 9. SETUP & ENVIRONMENT

Playwright:
  pip install playwright
  playwright install chromium

browser-use:
  pip install browser-use

Environment Variables:
  NVIDIA_API_KEY      - NVIDIA provider
  OPENAI_API_KEY      - OpenAI provider
  ANTHROPIC_API_KEY   - Anthropic provider
  GOOGLE_API_KEY      - Google provider
  Ollama:             - No API key needed (local)

---

## 10. SUMMARY

Dual-tier architecture:
  Tier 1: Fast, lightweight Playwright wrapper for simple checks
  Tier 2: Full-featured browser-use agent with vision & NLP for complex tasks

Both integrated into main agent loop with:
  - Thread-safe execution
  - Multi-LLM support
  - Vision capabilities
  - MCP integration
  - Cloud provider support

Total: 288 Python files, 15+ LLM providers, modular architecture

